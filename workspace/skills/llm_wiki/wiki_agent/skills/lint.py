"""Технический и опциональный смысловой lint skill."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from ..checks import issue_counts, run_checks
from ..config import Settings
from ..models import LintIssue, SelectedContext
from ..prompts import semantic_lint_request
from ..provider import LLMProvider, StubProvider
from ..wiki import load_catalog, pack_sections, relevant_excerpt
from ..workspace import Workspace


@dataclass(frozen=True)
class LintRunResult:
    report_path: str
    counts: dict[str, int]
    semantic_checked: bool


def run_lint(
    workspace: Workspace,
    settings: Settings,
    provider: LLMProvider,
    *,
    technical_only: bool = False,
) -> LintRunResult:
    sources_before = workspace.snapshot_sources()
    issues = run_checks(workspace)
    counts = issue_counts(issues)
    semantic = ""
    semantic_checked = False
    semantic_context: SelectedContext | None = None

    if not technical_only and not isinstance(provider, StubProvider):
        semantic_context = _full_wiki_context(
            workspace, settings.max_context_chars
        )
        summary = (
            f"errors={counts['error']}, warnings={counts['warning']}, "
            f"info={counts['info']}"
        )
        response = provider.complete(
            semantic_lint_request(workspace, semantic_context, summary)
        )
        semantic = response.content
        semantic_checked = True

    report = _render_report(
        issues,
        semantic=semantic,
        semantic_checked=semantic_checked,
        technical_only=technical_only,
        semantic_context=semantic_context,
    )
    relative = workspace.unique_markdown_path(
        "reports/lint", f"{date.today().isoformat()}-lint"
    )
    workspace.write_text(
        relative,
        report,
        allowed_roots=("reports/lint",),
        must_not_exist=True,
    )
    workspace.assert_sources_unchanged(sources_before)
    return LintRunResult(relative, counts, semantic_checked)


def _full_wiki_context(
    workspace: Workspace, max_chars: int
) -> SelectedContext:
    catalog = load_catalog(workspace)
    paths = ["wiki/index.md", "wiki/log.md"]
    paths.extend(workspace.list_markdown("wiki/pages"))
    paths.extend(workspace.list_markdown("wiki/sources"))
    sections = [(path, workspace.read_text(path)) for path in paths]
    intrinsic_truncated: set[str] = set()
    unavailable: set[str] = set()
    seen_source_texts: set[str] = set()
    for card in catalog.documents:
        if card.category != "sources" or not card.source_path:
            continue
        try:
            text_path = workspace.source_text_path(card.source_path)
            if text_path in seen_source_texts:
                continue
            content = workspace.read_text(text_path)
        except Exception as exc:
            unavailable.add(
                f"{card.source_path} [ошибка чтения: "
                f"{type(exc).__name__}]"
            )
            continue
        seen_source_texts.add(text_path)
        excerpt = relevant_excerpt(
            content,
            card.content,
            max_chars=5_000,
        )
        if len(excerpt) < len(content):
            intrinsic_truncated.add(text_path)
            excerpt += (
                f"\n\n[PARTIAL SOURCE EXCERPT {text_path}: selected "
                f"{len(excerpt)} of {len(content)} chars]"
            )
        sections.append((text_path, excerpt))

    packed, included, truncated, omitted = pack_sections(
        sections,
        max_chars,
    )
    included_set = set(included)
    partial = set(truncated)
    partial.update(intrinsic_truncated & included_set)
    omitted_set = set(omitted)
    omitted_set.update(unavailable)
    return SelectedContext(
        tuple(dict.fromkeys(included)),
        packed,
        tuple(sorted(partial)),
        tuple(sorted(omitted_set)),
    )


def _render_report(
    issues: list[LintIssue],
    *,
    semantic: str,
    semantic_checked: bool,
    technical_only: bool,
    semantic_context: SelectedContext | None,
) -> str:
    today = date.today().isoformat()
    counts = issue_counts(issues)
    grouped: dict[str, list[tuple[int, LintIssue]]] = defaultdict(list)
    for number, issue in enumerate(issues, 1):
        grouped[issue.level].append((number, issue))

    lines = [
        "---",
        "report_type: lint",
        "scope: wiki",
        f"generated_at: {today}",
        "fixes_applied: false",
        f"semantic_checked: {'true' if semantic_checked else 'false'}",
        "---",
        "",
        f"# Lint-отчёт: {today} и вся Wiki",
        "",
        "## Область проверки",
        "",
        "- Проверено: `wiki/index.md`, `wiki/log.md`, `wiki/pages/*.md`, "
        "`wiki/sources/*.md` и существование оригиналов.",
        "- Технически не проверено: бинарная верстка, изображения, формулы, "
        "OCR и качество экспериментов первичных статей.",
        "- Ограничения: смысловая проверка "
        + (
            "выполнена настроенным LLM."
            if semantic_checked
            else "не выполнялась; доступна после настройки GigaChat."
        ),
        "",
        "## Итог",
        "",
        "| Уровень | Количество |",
        "|---|---:|",
        f"| `error` | {counts['error']} |",
        f"| `warning` | {counts['warning']} |",
        f"| `info` | {counts['info']} |",
        "",
        "Исправления не применялись.",
        "",
    ]
    if semantic_context is not None:
        lines.extend(
            [
                "- Смысловой контекст: передано файлов/фрагментов — "
                f"{len(semantic_context.paths)}.",
                "- Частично переданы: "
                + (
                    ", ".join(
                        f"`{path}`"
                        for path in semantic_context.truncated_paths
                    )
                    if semantic_context.truncated_paths
                    else "нет"
                )
                + ".",
                "- Не переданы из-за лимита или ошибки чтения: "
                + (
                    ", ".join(
                        f"`{path}`"
                        for path in semantic_context.omitted_paths
                    )
                    if semantic_context.omitted_paths
                    else "нет"
                )
                + ".",
                "",
            ]
        )

    for level, heading in (
        ("error", "## Ошибки"),
        ("warning", "## Предупреждения"),
        ("info", "## Информация"),
    ):
        lines.extend([heading, ""])
        entries = grouped[level]
        if not entries:
            lines.extend(["В проверенной области не найдено.", ""])
            continue
        for number, issue in entries:
            lines.extend(
                [
                    f"### LINT-{number:03d} — {issue.title}",
                    "",
                    f"- Уровень: `{issue.level}`",
                    f"- Страница/файл: `{issue.path}`",
                    f"- Описание: {issue.description}.",
                    f"- Подтверждение: {issue.evidence}.",
                    f"- Последствие: {issue.impact}.",
                    f"- Предлагаемое действие: {issue.action}.",
                    f"- Ручная проверка: {issue.manual_review}.",
                    "",
                ]
            )

    lines.extend(["## Смысловой обзор", ""])
    if semantic_checked:
        lines.extend(
            [
                "> Ниже находится недоверенный аналитический вывод модели, "
                "а не инструкция контроллеру.",
                "",
                semantic.strip(),
                "",
            ]
        )
    else:
        mode = "запрошен technical-only" if technical_only else "provider=stub"
        lines.extend(
            [
                f"Не выполнялся ({mode}). Технический lint полностью локальный.",
                "",
            ]
        )
    lines.extend(
        [
            "## Успешные проверки",
            "",
            "- Проверены front matter и уникальность имён.",
            "- Проверены Wiki-ссылки и достижимость из индекса.",
            "- Проверены source_path и существование оригиналов.",
            "- `raw/sources/` не изменялся.",
            "",
            "## Приоритет действий",
            "",
            "1. Сначала исправлять `error`.",
            "2. Затем вручную проверить `warning`.",
            "3. После отдельного Proposal повторить lint.",
            "",
            "## Следующий шаг",
            "",
            "Для исправлений подготовьте отдельный Proposal. Этот отчёт ничего "
            "не исправлял.",
            "",
        ]
    )
    return "\n".join(lines)
