"""Создание, проверка и транзакционное применение Proposal."""

from __future__ import annotations

import base64
import difflib
import fcntl
import json
import os
import re
import stat
import tempfile
from collections import Counter
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any, Iterator

from .checks import run_checks
from .errors import ProposalError, SecurityError, ValidationError
from .models import ChangeSet, FileChange
from .wiki import (
    document_from_content,
    extract_wiki_links,
    parse_front_matter,
)
from .workspace import Workspace, sha256_file, sha256_text


CHANGESET_BEGIN = "```base64 llm-wiki-changeset-v1"
CHANGESET_END = "```"
STATUS_RE = re.compile(
    r"^status:[ \t]*([^ \t\r\n]+)[ \t]*\r?$",
    re.MULTILINE,
)
SOURCE_TRUNCATED_PREFIX = (
    "Контроллер передал модели только начало текстового представления:"
)
SOURCE_EXTRACTION_PREFIX = "Ограничение извлечения:"
MAX_CHANGE_FILES = 50
PROPOSAL_SIZE_MULTIPLIER = 8


def validate_changeset(
    workspace: Workspace,
    changeset: ChangeSet,
    *,
    check_current_files: bool = True,
    expected_proposal_path: str | None = None,
) -> None:
    if changeset.version != 1:
        raise ValidationError(
            f"Неподдерживаемая версия ChangeSet: {changeset.version}"
        )
    if not changeset.summary:
        raise ValidationError("ChangeSet не содержит summary")
    if not changeset.changes:
        raise ValidationError("ChangeSet не содержит изменений")
    if len(changeset.changes) > MAX_CHANGE_FILES:
        raise ValidationError(
            f"ChangeSet содержит больше {MAX_CHANGE_FILES} файлов"
        )
    if changeset.links_removed:
        raise SecurityError(
            "Автоматическое удаление Wiki-связей запрещено в MVP"
        )

    source = workspace.source_path(changeset.source_path)
    if sha256_file(source) != changeset.source_sha256:
        raise ValidationError(
            "Оригинальный источник изменился после создания Proposal"
        )

    paths: set[str] = set()
    path_identities: set[str] = set()
    targets: set[Path] = set()
    total_content_chars = 0
    for change in changeset.changes:
        if change.action not in {"create", "update"}:
            raise SecurityError(
                f"Действие {change.action!r} запрещено: {change.path}"
            )
        if change.path in paths:
            raise ValidationError(f"Повтор пути в ChangeSet: {change.path}")
        paths.add(change.path)
        identity = workspace.path_identity_key(change.path)
        if identity in path_identities:
            raise ValidationError(
                "Пути ChangeSet конфликтуют по регистру или Unicode: "
                f"{change.path}"
            )
        path_identities.add(identity)
        target = workspace.validate_ingest_target(change.path)
        canonical_path = workspace.relative(target)
        if canonical_path != change.path:
            raise SecurityError(
                f"Неканонический путь ChangeSet: {change.path}"
            )
        if target in targets:
            raise ValidationError(
                f"Несколько изменений указывают на {canonical_path}"
            )
        targets.add(target)
        if not change.after_content.strip():
            raise ValidationError(f"Пустое итоговое содержимое: {change.path}")
        if len(change.after_content) > workspace.max_file_chars:
            raise ValidationError(
                f"Итоговый файл превышает лимит "
                f"{workspace.max_file_chars} символов: {change.path}"
            )
        total_content_chars += len(change.after_content)
        total_content_chars += len(change.before_content or "")
        if "\x00" in change.after_content:
            raise ValidationError(f"NUL-байт в содержимом: {change.path}")
        if check_current_files:
            if change.action == "create" and target.exists():
                raise ValidationError(
                    f"Файл для create уже существует: {change.path}"
                )
            if change.action == "update":
                if not target.is_file():
                    raise ValidationError(
                        f"Файл для update отсутствует: {change.path}"
                    )
                current = workspace.read_text(change.path)
                if current != change.before_content:
                    raise ValidationError(
                        "Файл изменился после создания Proposal: "
                        f"{change.path}"
                    )
        if change.action == "update" and change.before_content is not None:
            preservation_errors = _preservation_errors(
                change.path,
                change.before_content,
                change.after_content,
            )
            if preservation_errors:
                raise ValidationError(
                    "Автоматический ingest может только дополнять "
                    f"{change.path}: "
                    + " | ".join(preservation_errors[:5])
                )
            removed_links = set(extract_wiki_links(change.before_content)) - set(
                extract_wiki_links(change.after_content)
            )
            if removed_links:
                raise SecurityError(
                    f"Автоматическое удаление связей запрещено в {change.path}: "
                    + ", ".join(f"[[{item}]]" for item in sorted(removed_links))
                )

    if total_content_chars > workspace.max_file_chars * 2:
        raise ValidationError(
            "Суммарный объём ChangeSet слишком велик для одного Proposal; "
            "сузьте ingest"
        )

    required = {"wiki/index.md", "wiki/log.md"}
    missing = required - paths
    if missing:
        raise ValidationError(
            "Ingest обязан обновлять индекс и журнал; отсутствуют: "
            + ", ".join(sorted(missing))
        )
    log_required_change = next(
        change for change in changeset.changes
        if change.path == "wiki/log.md"
    )
    if (
        log_required_change.action != "update"
        or log_required_change.before_content is None
    ):
        raise ValidationError(
            "wiki/log.md должен существовать до ingest и обновляться "
            "строго append-only"
        )
    index_required_change = next(
        change for change in changeset.changes
        if change.path == "wiki/index.md"
    )
    if (
        index_required_change.action != "update"
        or index_required_change.before_content is None
    ):
        raise ValidationError(
            "wiki/index.md должен существовать до ingest; разрешены только "
            "явные добавления к существующему тексту"
        )

    source_cards = [
        change
        for change in changeset.changes
        if change.path.startswith("wiki/sources/")
    ]
    if not source_cards:
        raise ValidationError("Ingest обязан создать или обновить карточку")
    matching_cards = [
        change
        for change in source_cards
        if _front_matter_source_path(change.after_content)
        == changeset.source_path
    ]
    if not matching_cards:
        raise ValidationError(
            "Ни одна карточка не ссылается на исходный source_path"
        )
    matching_card_documents = [
        document_from_content(change.path, change.after_content)
        for change in matching_cards
    ]
    matching_card_titles = {
        document.title
        for document in matching_card_documents
        if document.title
    }
    if not matching_card_titles:
        raise ValidationError(
            "Карточка текущего источника не содержит title"
        )
    source_truncation_limitations = [
        limitation
        for limitation in changeset.reading_limitations
        if limitation.startswith(SOURCE_TRUNCATED_PREFIX)
    ]
    source_extraction_limitations = [
        limitation
        for limitation in changeset.reading_limitations
        if limitation.startswith(SOURCE_EXTRACTION_PREFIX)
    ]
    incomplete_source_limitations = [
        *source_truncation_limitations,
        *source_extraction_limitations,
    ]
    if incomplete_source_limitations and not all(
        document.status == "partial"
        for document in matching_card_documents
    ):
        raise ValidationError(
            "При неполном чтении или автоматическом извлечении каждая "
            "карточка источника должна иметь status: partial"
        )
    for document in matching_card_documents:
        if not incomplete_source_limitations:
            continue
        limitation_section = _markdown_section(
            document.content,
            "Ограничения чтения",
        )
        missing_limitations = [
            limitation
            for limitation in incomplete_source_limitations
            if limitation not in limitation_section
        ]
        if missing_limitations:
            raise ValidationError(
                f"{document.path}: раздел `## Ограничения чтения` должен "
                "дословно содержать ограничение контроллера"
            )
        if re.search(
            r"(?im)^[ \t]*(?:[-*][ \t]+)?"
            r"(?:нет|не выявлены)[.!]?[ \t]*$",
            limitation_section,
        ):
            raise ValidationError(
                f"{document.path}: при неполном чтении раздел ограничений "
                "не может одновременно утверждать «Нет»"
            )

    page_changes = [
        change
        for change in changeset.changes
        if change.path.startswith("wiki/pages/")
    ]
    if not page_changes:
        raise ValidationError(
            "Ingest должен точечно обновить или создать тематическую страницу"
        )
    for page_change in page_changes:
        source_links = set(
            _source_section_links(page_change.after_content)
        )
        if not (source_links & matching_card_titles):
            expected = ", ".join(
                f"[[{title}]]" for title in sorted(matching_card_titles)
            )
            raise ValidationError(
                f"{page_change.path}: раздел `## Источники` должен "
                f"ссылаться на карточку текущего источника: {expected}"
            )

    log_change = next(
        (
            change
            for change in changeset.changes
            if change.path == "wiki/log.md"
        ),
        None,
    )
    if expected_proposal_path is not None:
        log_added_content = (
            log_change.after_content[len(log_change.before_content) :]
            if (
                log_change is not None
                and log_change.before_content is not None
            )
            else ""
        )
        if f"`{expected_proposal_path}`" not in log_added_content:
            raise ValidationError(
                "Новая запись в добавленном конце wiki/log.md должна "
                "ссылаться на точный Proposal: "
                f"`{expected_proposal_path}`"
            )

    overrides = {
        change.path: change.after_content for change in changeset.changes
    }
    baseline_errors = [
        issue
        for issue in run_checks(workspace)
        if issue.level == "error"
    ]
    virtual_all_errors = [
        issue
        for issue in run_checks(
            workspace,
            overrides=overrides,
            known_proposals=(
                {expected_proposal_path}
                if expected_proposal_path is not None
                else set()
            ),
        )
        if issue.level == "error"
    ]
    virtual_errors = _new_issues(baseline_errors, virtual_all_errors)
    if virtual_errors:
        details = "; ".join(
            f"{issue.path}: {issue.title} ({issue.evidence})"
            for issue in virtual_errors[:10]
        )
        raise ValidationError(
            "ChangeSet создаёт новые lint-ошибки: " + details
        )


def render_proposal(changeset: ChangeSet) -> str:
    today = date.today().isoformat()
    machine_payload = base64.b64encode(
        json.dumps(changeset.to_dict(), ensure_ascii=False).encode("utf-8")
    ).decode("ascii")
    created = [change for change in changeset.changes if change.action == "create"]
    updated = [change for change in changeset.changes if change.action == "update"]
    lines = [
        "---",
        "operation: ingest",
        "status: proposed",
        f"created_at: {today}",
        "source_path: "
        + json.dumps(changeset.source_path, ensure_ascii=False),
        "---",
        "",
        "## Машиночитаемый ChangeSet",
        "",
        "Этот блок читает только локальный `apply`. Он закодирован, чтобы",
        "содержимое создаваемых Markdown-файлов не могло закрыть блок раньше",
        "времени. Человек проверяет полный текст и diff ниже.",
        "",
        CHANGESET_BEGIN,
        machine_payload,
        CHANGESET_END,
        "",
        f"# Proposal: ingest {Path(changeset.source_path).name}",
        "",
        "## Цель",
        "",
        changeset.summary,
        "",
        "## Основание и область",
        "",
        f"- Источник: `{changeset.source_path}`.",
        "- Ограничения чтения:",
    ]
    lines.extend(_bullets(changeset.reading_limitations, "Не выявлены."))
    lines.extend(
        [
            "- Не входит: удаление, перемещение, переименование, изменение "
            "первоисточников и расширение структуры Wiki.",
            "",
            "## План",
            "",
        ]
    )
    for number, change in enumerate(changeset.changes, 1):
        lines.append(f"{number}. `{change.path}` — {change.reason}")

    lines.extend(["", "## Создаваемые файлы", ""])
    lines.extend(_change_table(created))
    lines.extend(["", "## Обновляемые файлы", ""])
    lines.extend(_change_table(updated))
    lines.extend(
        [
            "",
            "## Удаления и переименования",
            "",
            "Нет. Автоматическое удаление и переименование запрещено.",
            "",
            "## Связи",
            "",
            "### Добавляемые",
            "",
        ]
    )
    lines.extend(_bullets(changeset.links_added, "Нет."))
    lines.extend(["", "### Удаляемые", "", "Нет.", ""])
    lines.extend(
        [
            "## Конфликты, дубли и неопределённости",
            "",
        ]
    )
    lines.extend(
        _bullets(
            changeset.conflicts,
            "Не выявлены в проверенной области.",
        )
    )
    lines.extend(["", "## Полное содержимое новых файлов", ""])
    if created:
        for change in created:
            lines.extend(
                [
                    f"### `{change.path}`",
                    "",
                    *_fenced_block(
                        "markdown", change.after_content.rstrip()
                    ),
                    "",
                ]
            )
    else:
        lines.extend(["Нет.", ""])

    lines.extend(["## Diff существующих файлов", ""])
    if updated:
        for change in updated:
            diff = _unified_diff(change)
            lines.extend(
                [
                    f"### `{change.path}`",
                    "",
                    *_fenced_block("diff", diff.rstrip()),
                    "",
                ]
            )
    else:
        lines.extend(["Нет.", ""])

    lines.extend(["## Проверки после применения", ""])
    checks = changeset.verification or [
        "Изменены только перечисленные файлы.",
        "Первоисточники не изменены.",
        "Front matter корректен.",
        "Wiki-ссылки разрешаются.",
        "Значимые утверждения имеют источники.",
        "Индекс и журнал соответствуют результату.",
    ]
    lines.extend(f"- [ ] {item}" for item in checks)
    lines.extend(
        [
            "",
            "## Подтверждение и результат",
            "",
            "- Подтвердил: ожидается.",
            "- Дата применения: ожидается.",
            "- Фактический результат: ожидается.",
            "- Отклонения: ожидается.",
            "",
        ]
    )
    return "\n".join(lines)


def proposal_path_for_source(
    workspace: Workspace, source_path: str
) -> str:
    stem = _slug(Path(source_path).stem)
    return workspace.unique_markdown_path(
        "proposals", f"{date.today().isoformat()}-ingest-{stem}"
    )


def save_proposal(
    workspace: Workspace,
    changeset: ChangeSet,
    proposal_path: str,
) -> str:
    relative = workspace.relative(
        workspace.resolve(proposal_path, allowed_roots=("proposals",))
    )
    if relative != proposal_path:
        raise SecurityError(
            f"Неканонический путь Proposal: {proposal_path}"
        )
    content = render_proposal(changeset)
    if len(content) > workspace.max_file_chars * PROPOSAL_SIZE_MULTIPLIER:
        raise ValidationError(
            "Proposal превышает безопасный лимит; сузьте область ingest"
        )
    workspace.write_text(
        relative,
        content,
        allowed_roots=("proposals",),
        must_not_exist=True,
    )
    return relative


def load_changeset_from_proposal(content: str) -> ChangeSet:
    start = content.find(CHANGESET_BEGIN)
    if start < 0:
        raise ProposalError("В Proposal нет машиночитаемого ChangeSet")
    start += len(CHANGESET_BEGIN)
    end = content.find(CHANGESET_END, start)
    if end < 0:
        raise ProposalError("ChangeSet в Proposal не закрыт")
    encoded = content[start:end].strip()
    try:
        raw_json = base64.b64decode(encoded, validate=True).decode("utf-8")
        value = json.loads(raw_json)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProposalError(
            f"Некорректный кодированный ChangeSet: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise ProposalError("ChangeSet должен быть JSON-объектом")
    try:
        return ChangeSet.from_dict(value)
    except (TypeError, ValueError) as exc:
        raise ProposalError(
            f"ChangeSet не соответствует ожидаемой схеме: {exc}"
        ) from exc


def proposal_revision(content: str) -> str:
    return sha256_text(content)[:16]


def render_change_preview(changeset: ChangeSet) -> str:
    parts: list[str] = []
    for change in changeset.changes:
        parts.append(
            f"\n===== {change.action.upper()}: {change.path} =====\n"
        )
        parts.append(_unified_diff(change) or "(нет diff)\n")
    return "".join(parts).lstrip()


def apply_proposal(
    workspace: Workspace,
    proposal_path: str,
    *,
    confirmed_path: str,
    expected_revision: str | None = None,
) -> list[str]:
    """Сериализовать локальные apply и применить точный ChangeSet."""

    with _advisory_lock(workspace.resolve("proposals", must_exist=True)):
        return _apply_proposal_locked(
            workspace,
            proposal_path,
            confirmed_path=confirmed_path,
            expected_revision=expected_revision,
        )


def _apply_proposal_locked(
    workspace: Workspace,
    proposal_path: str,
    *,
    confirmed_path: str,
    expected_revision: str | None = None,
) -> list[str]:
    """Применить только точный подтверждённый ChangeSet без вызова LLM."""

    path = workspace.resolve(
        proposal_path, must_exist=True, allowed_roots=("proposals",)
    )
    relative = workspace.relative(path)
    if confirmed_path != relative:
        raise ProposalError(
            "Подтверждение не совпадает с точным путём Proposal"
        )
    content = workspace.read_text(
        relative,
        max_chars=workspace.max_file_chars * PROPOSAL_SIZE_MULTIPLIER,
    )
    _proposal_front_matter(content, required_status="proposed")
    revision = proposal_revision(content)
    if expected_revision is not None and expected_revision != revision:
        raise ProposalError(
            f"Редакция Proposal изменилась: ожидалась {expected_revision}, "
            f"сейчас {revision}"
        )

    changeset = load_changeset_from_proposal(content)
    sources_before = workspace.snapshot_sources()
    if (
        sources_before.get(changeset.source_path)
        != changeset.source_sha256
    ):
        raise ValidationError(
            "Оригинальный источник не совпадает с версией из Proposal"
        )
    validate_changeset(
        workspace,
        changeset,
        check_current_files=True,
        expected_proposal_path=relative,
    )
    baseline_errors = [
        issue
        for issue in run_checks(workspace)
        if issue.level == "error"
    ]

    applied_proposal = _mark_applied(content, len(changeset.changes))
    target_contents: dict[Path, bytes] = {}
    expected_contents: dict[Path, bytes | None] = {}
    changed_relatives: list[str] = []
    for change in changeset.changes:
        target = workspace.validate_ingest_target(change.path)
        target_contents[target] = change.after_content.encode("utf-8")
        expected_contents[target] = (
            change.before_content.encode("utf-8")
            if change.before_content is not None
            else None
        )
        changed_relatives.append(change.path)
    target_contents[path] = applied_proposal.encode("utf-8")
    expected_contents[path] = content.encode("utf-8")

    backups = _transactional_replace(
        target_contents,
        expected_contents=expected_contents,
    )
    try:
        workspace.assert_sources_unchanged(sources_before)
        post_all_errors = [
            issue
            for issue in run_checks(workspace)
            if issue.level == "error"
        ]
        new_errors = _new_issues(baseline_errors, post_all_errors)
        if new_errors:
            raise ProposalError(
                "После применения появились новые lint-ошибки: "
                + "; ".join(
                    f"{item.path}: {item.title}" for item in new_errors[:10]
                )
            )
    except Exception:
        _restore_backups(backups)
        raise
    return changed_relatives


def extract_json_object(text: str) -> dict[str, Any]:
    """Принять ровно один JSON-объект и запретить посторонний текст."""

    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    if not stripped.startswith("{"):
        raise ValidationError(
            "Перед JSON-объектом модель вернула посторонний текст"
        )
    decoder = json.JSONDecoder()
    try:
        value, end = decoder.raw_decode(stripped)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"Некорректный JSON модели: {exc}") from exc
    remainder = stripped[end:].strip()
    if remainder:
        raise ValidationError(
            "После JSON-объекта модель вернула посторонний текст"
        )
    if not isinstance(value, dict):
        raise ValidationError("Ответ модели должен быть JSON-объектом")
    return value


def _transactional_replace(
    target_contents: dict[Path, bytes],
    *,
    expected_contents: dict[Path, bytes | None],
) -> dict[Path, tuple[bytes | None, int | None]]:
    """Подготовить все файлы, затем заменить; при ошибке откатить backups."""

    if set(target_contents) != set(expected_contents):
        raise ProposalError("Внутренняя ошибка CAS: набор целей не совпадает")
    _assert_expected_contents(expected_contents)
    backups: dict[Path, tuple[bytes | None, int | None]] = {}
    for path in target_contents:
        if path.exists():
            backups[path] = (
                path.read_bytes(),
                stat.S_IMODE(path.stat().st_mode),
            )
        else:
            backups[path] = (None, None)
    temporary: dict[Path, Path] = {}
    replaced: list[Path] = []
    try:
        for target, payload in target_contents.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(
                prefix=f".{target.name}.",
                suffix=".apply.tmp",
                dir=target.parent,
            )
            temp = Path(name)
            os.fchmod(descriptor, backups[target][1] or 0o644)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            temporary[target] = temp
        for target, temp in temporary.items():
            _assert_one_expected(target, expected_contents[target])
            os.replace(temp, target)
            replaced.append(target)
    except Exception:
        for target in reversed(replaced):
            payload, mode = backups[target]
            if payload is None:
                target.unlink(missing_ok=True)
            else:
                _replace_bytes(target, payload, mode=mode or 0o644)
        raise
    finally:
        for temp in temporary.values():
            temp.unlink(missing_ok=True)
    return backups


def _restore_backups(
    backups: dict[Path, tuple[bytes | None, int | None]]
) -> None:
    for target, (payload, mode) in backups.items():
        if payload is None:
            target.unlink(missing_ok=True)
        else:
            _replace_bytes(target, payload, mode=mode or 0o644)


def _replace_bytes(path: Path, payload: bytes, *, mode: int) -> None:
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".rollback.tmp", dir=path.parent
    )
    temp = Path(name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _assert_expected_contents(
    expected_contents: dict[Path, bytes | None],
) -> None:
    for path, expected in expected_contents.items():
        _assert_one_expected(path, expected)


def _assert_one_expected(path: Path, expected: bytes | None) -> None:
    current = path.read_bytes() if path.exists() else None
    if current != expected:
        raise ProposalError(
            "Файл изменился непосредственно перед commit; ничего не "
            f"перезаписано: {path}"
        )


@contextmanager
def _advisory_lock(directory: Path) -> Iterator[None]:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _mark_applied(content: str, changed_count: int) -> str:
    today = date.today().isoformat()
    status_match = _proposal_front_matter(
        content,
        required_status="proposed",
    )
    result = (
        content[: status_match.start()]
        + f"status: applied\napplied_at: {today}"
        + content[status_match.end() :]
    )
    old = (
        "- Подтвердил: ожидается.\n"
        "- Дата применения: ожидается.\n"
        "- Фактический результат: ожидается.\n"
        "- Отклонения: ожидается."
    )
    new = (
        "- Подтвердил: пользователь точным путём Proposal.\n"
        f"- Дата применения: {today}.\n"
        f"- Фактический результат: применено файлов: {changed_count}.\n"
        "- Отклонения: нет."
    )
    position = result.rfind(old)
    if position < 0:
        raise ProposalError(
            "В Proposal отсутствует ожидаемый блок результата; применение "
            "остановлено"
        )
    return result[:position] + new + result[position + len(old) :]


def _proposal_front_matter(
    content: str,
    *,
    required_status: str,
) -> re.Match[str]:
    parsed = parse_front_matter(content)
    if parsed.errors:
        raise ProposalError(
            "Некорректный front matter Proposal: "
            + "; ".join(parsed.errors)
        )
    if parsed.values.get("operation") != "ingest":
        raise ProposalError(
            "Proposal должен содержать `operation: ingest` в первом "
            "front matter"
        )
    if parsed.values.get("status") != required_status:
        raise ProposalError(
            "Применить можно только Proposal со "
            f"`status: {required_status}` в первом front matter"
        )
    if required_status == "proposed" and "applied_at" in parsed.values:
        raise ProposalError(
            "Proposal со status: proposed не должен содержать applied_at"
        )

    boundaries = list(
        re.finditer(r"^---[ \t]*\r?$", content, re.MULTILINE)
    )
    if len(boundaries) < 2 or boundaries[0].start() != 0:
        raise ProposalError("Не найдены точные границы front matter Proposal")
    front_end = boundaries[1].end()
    status_matches = list(STATUS_RE.finditer(content, 0, front_end))
    if len(status_matches) != 1:
        raise ProposalError(
            "Front matter Proposal должен содержать ровно одно поле status"
        )
    return status_matches[0]


def _unified_diff(change: FileChange) -> str:
    before = (change.before_content or "").splitlines(keepends=True)
    after = change.after_content.splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            before,
            after,
            fromfile=f"a/{change.path}",
            tofile=f"b/{change.path}",
        )
    )


def _fenced_block(language: str, content: str) -> list[str]:
    runs = [len(match.group(0)) for match in re.finditer(r"`+", content)]
    fence = "`" * max(3, (max(runs) + 1) if runs else 3)
    return [f"{fence}{language}", content, fence]


def _front_matter_source_path(content: str) -> str | None:
    document = document_from_content("wiki/sources/_virtual.md", content)
    return document.source_path


def _change_table(changes: list[FileChange]) -> list[str]:
    if not changes:
        return ["Нет."]
    lines = ["| Файл | Причина |", "|---|---|"]
    lines.extend(
        f"| `{change.path}` | {change.reason.replace('|', '—')} |"
        for change in changes
    )
    return lines


def _bullets(values: list[str], fallback: str) -> list[str]:
    if not values:
        return [f"- {fallback}"]
    return [f"- {value}" for value in values]


def _slug(value: str) -> str:
    slug = re.sub(r"[^\w.-]+", "-", value.casefold(), flags=re.UNICODE)
    slug = slug.strip("-._")
    return slug or "source"


def _unsafe_update_reasons(before: str, after: str) -> list[str]:
    """Разрешить вставки без изменения порядка и Markdown-контекста."""

    before_lines = _normalized_update_lines(before)
    after_lines = _normalized_update_lines(after)
    cursor = 0
    matched: list[tuple[int, int]] = []
    for before_index, line in enumerate(before_lines):
        match_index = next(
            (
                index
                for index in range(cursor, len(after_lines))
                if after_lines[index] == line
            ),
            None,
        )
        if match_index is None:
            preview = line.strip()[:160] or "<пустая строка>"
            return [
                "старая строка удалена, изменена или переставлена: "
                + preview
            ]
        matched.append((before_index, match_index))
        cursor = match_index + 1

    before_dangerous = Counter(
        line for line in before_lines if _dangerous_markdown_boundary(line)
    )
    after_dangerous = Counter(
        line for line in after_lines if _dangerous_markdown_boundary(line)
    )
    added_dangerous = after_dangerous - before_dangerous
    if added_dangerous:
        return [
            "в update добавлена граница комментария, HTML или code fence: "
            + next(iter(added_dangerous)).strip()[:160]
        ]

    before_context = _heading_context_by_line(before_lines)
    after_context = _heading_context_by_line(after_lines)
    for before_index, after_index in matched:
        line = before_lines[before_index]
        if (
            not line.strip()
            or line == "__LLM_WIKI_UPDATED_AT__"
            or re.match(r"^[ \t]{0,3}#{1,6}[ \t]+", line)
        ):
            continue
        if before_context[before_index] != after_context[after_index]:
            return [
                "изменён Markdown-раздел существующей строки: "
                + line.strip()[:160]
            ]
    return []


def _preservation_errors(
    path: str,
    before: str,
    after: str,
) -> list[str]:
    """Применить файловую политику сохранения к существующему Markdown."""

    if path == "wiki/log.md":
        if not after.startswith(before):
            return [
                "старая история должна быть точным байтовым префиксом "
                "итогового файла"
            ]
        if not after[len(before) :].strip():
            return ["новая запись должна быть добавлена только в конец"]
        return []

    if path == "wiki/index.md" or path.startswith("wiki/pages/"):
        strict_errors = _strict_line_insertion_errors(before, after)
        if strict_errors:
            return strict_errors
        return _unsafe_update_reasons(before, after)

    return _unsafe_update_reasons(before, after)


def _strict_line_insertion_errors(before: str, after: str) -> list[str]:
    """Разрешить только вставку новых строк без нормализации старых."""

    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    cursor = 0
    for before_index, line in enumerate(before_lines):
        is_unterminated_last_line = (
            before_index == len(before_lines) - 1
            and not line.endswith(("\n", "\r"))
        )
        match_index = next(
            (
                index
                for index in range(cursor, len(after_lines))
                if (
                    after_lines[index] == line
                    or (
                        is_unterminated_last_line
                        and after_lines[index] in {line + "\n", line + "\r\n"}
                    )
                )
            ),
            None,
        )
        if match_index is None:
            preview = line.rstrip("\r\n")[:160] or "<пустая строка>"
            return [
                "существующая строка удалена, изменена или переставлена: "
                + preview
            ]
        cursor = match_index + 1
    return []


def _normalized_update_lines(content: str) -> list[str]:
    lines = [line.rstrip() for line in content.splitlines()]
    front_end = _front_matter_end_line(lines)
    for index in range(1, front_end):
        if re.match(r"^updated_at:[ \t]*", lines[index]):
            lines[index] = "__LLM_WIKI_UPDATED_AT__"
    return lines


def _front_matter_end_line(lines: list[str]) -> int:
    if not lines or lines[0].strip() != "---":
        return 0
    for index, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            return index
    return 0


def _dangerous_markdown_boundary(line: str) -> bool:
    if "<!--" in line or "-->" in line:
        return True
    if re.match(r"^[ \t]{0,3}(?:`{3,}|~{3,})", line):
        return True
    return bool(
        re.match(
            r"^[ \t]*</?[A-Za-z][^>]*>[ \t]*$",
            line,
        )
    )


def _heading_context_by_line(
    lines: list[str],
) -> list[tuple[tuple[int, str], ...]]:
    contexts: list[tuple[tuple[int, str], ...]] = []
    stack: dict[int, str] = {}
    front_end = _front_matter_end_line(lines)
    for index, line in enumerate(lines):
        if index <= front_end:
            contexts.append(())
            continue
        heading = re.match(
            r"^[ \t]{0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$",
            line,
        )
        if heading is not None:
            level = len(heading.group(1))
            stack = {
                key: value
                for key, value in stack.items()
                if key < level
            }
            stack[level] = heading.group(2).strip()
        contexts.append(tuple(sorted(stack.items())))
    return contexts


def _source_section_links(content: str) -> list[str]:
    return extract_wiki_links(_markdown_section(content, "Источники"))


def _markdown_section(content: str, heading: str) -> str:
    match = re.search(
        rf"^##[ \t]+{re.escape(heading)}[ \t]*$",
        content,
        re.MULTILINE,
    )
    if match is None:
        return ""
    remainder = content[match.end() :]
    next_heading = re.search(r"^##\s+", remainder, re.MULTILINE)
    return (
        remainder[: next_heading.start()]
        if next_heading is not None
        else remainder
    )


def _new_issues(
    before: list[Any],
    after: list[Any],
) -> list[Any]:
    """Сравнить lint-проблемы как multiset, не теряя повторы."""

    remaining = Counter(issue.fingerprint() for issue in before)
    result = []
    for issue in after:
        fingerprint = issue.fingerprint()
        if remaining[fingerprint] > 0:
            remaining[fingerprint] -= 1
        else:
            result.append(issue)
    return result
