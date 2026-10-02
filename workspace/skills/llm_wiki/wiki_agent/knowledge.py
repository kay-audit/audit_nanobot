"""Сборка безопасного ChangeSet из смыслового ответа модели."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any

from .checks import PAGE_CATEGORIES
from .errors import ValidationError
from .models import ChangeSet, FileChange
from .proposal import extract_json_object, validate_changeset
from .wiki import WikiDocument, load_catalog
from .workspace import Workspace


MAX_TOPICS = 8
MAX_CLAIMS_PER_TOPIC = 24
MAX_TEXT = 2_000


@dataclass(frozen=True)
class KnowledgeTopic:
    title: str
    summary: str
    claims: tuple[str, ...]
    aliases: tuple[str, ...]
    tags: tuple[str, ...]
    category: str
    index_section: str
    related_titles: tuple[str, ...]
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class KnowledgeDraft:
    summary: str
    source_title: str
    source_summary: str
    source_limitations: tuple[str, ...]
    conflicts: tuple[str, ...]
    topics: tuple[KnowledgeTopic, ...]


def changeset_from_knowledge_model(
    workspace: Workspace,
    source_path: str,
    source_text_path: str,
    model_response: str,
    *,
    source_sha256: str,
    proposal_path: str,
    controller_limitations: list[str] | None = None,
) -> ChangeSet:
    """Преобразовать знания модели в полностью локальную Wiki-транзакцию."""

    draft = parse_knowledge_draft(model_response, source_path=source_path)
    catalog = load_catalog(workspace)
    normalized_topics, normalization_conflicts = _normalize_topics_for_catalog(
        draft.topics,
        catalog.documents,
    )
    draft = replace(
        draft,
        topics=normalized_topics,
        conflicts=tuple(
            _deduplicate([*draft.conflicts, *normalization_conflicts])
        ),
    )
    today = date.today().isoformat()
    controller_limitations = list(controller_limitations or [])
    reading_limitations = _deduplicate(
        [*controller_limitations, *draft.source_limitations]
    )
    topic_titles = {topic.title for topic in draft.topics}
    normalized_topic_titles = {
        topic.title.casefold() for topic in draft.topics
    }
    if len(normalized_topic_titles) != len(draft.topics):
        raise ValidationError("Смысловой ответ содержит повтор title темы")
    source_title = _unique_source_title(
        draft.source_title,
        source_path,
        catalog.documents,
        reserved_titles=topic_titles,
    )
    source_card_path = f"wiki/sources/{source_title}.md"
    workspace.validate_ingest_target(source_card_path)

    page_changes: list[FileChange] = []
    page_documents: list[WikiDocument | None] = []

    known_page_titles = {
        document.title
        for document in catalog.documents
        if document.path.startswith("wiki/pages/") and document.title
    }
    conflicts = list(draft.conflicts)
    links_added: list[str] = []
    for topic in draft.topics:
        existing = _resolve_topic(
            catalog.documents,
            topic.title,
            expected_path=f"wiki/pages/{topic.title}.md",
        )
        related = []
        for related_title in topic.related_titles:
            if (
                related_title in known_page_titles
                or related_title in topic_titles
            ):
                related.append(related_title)
            else:
                conflicts.append(
                    f"Связь [[{topic.title}]] → [[{related_title}]] "
                    "не добавлена: цель отсутствует в текущей Wiki и "
                    "смысловом ответе."
                )
        if existing is None:
            path = f"wiki/pages/{topic.title}.md"
            workspace.validate_ingest_target(path)
            after = _render_new_page(
                topic,
                source_title,
                related,
                today=today,
            )
            page_changes.append(
                FileChange(
                    action="create",
                    path=path,
                    reason=(
                        "Контроллер создаёт самостоятельную тему из "
                        "смыслового ответа модели."
                    ),
                    before_content=None,
                    after_content=after,
                )
            )
            page_documents.append(None)
        else:
            after = _append_topic_knowledge(
                existing.content,
                topic,
                source_title,
                related,
                today=today,
            )
            page_changes.append(
                FileChange(
                    action="update",
                    path=existing.path,
                    reason=(
                        "Контроллер точечно дополняет существующую тему, "
                        "сохраняя старые строки."
                    ),
                    before_content=existing.content,
                    after_content=after,
                )
            )
            page_documents.append(existing)
        links_added.append(
            f"[[{topic.title}]] → [[{source_title}]]: "
            "утверждения прослеживаются до текущего источника."
        )
        links_added.extend(
            f"[[{topic.title}]] → [[{related_title}]]: смысловая связь."
            for related_title in related
        )

    card_content = _render_source_card(
        source_title,
        source_path,
        source_text_path,
        draft,
        reading_limitations,
        today=today,
    )
    source_change = FileChange(
        action="create",
        path=source_card_path,
        reason=(
            "Контроллер создаёт карточку с точным локальным source_path "
            "и ограничениями чтения."
        ),
        before_content=None,
        after_content=card_content,
    )
    links_added.extend(
        f"[[{source_title}]] → [[{topic.title}]]: "
        "карточка перечисляет интегрированную тему."
        for topic in draft.topics
    )

    index_before = workspace.read_text("wiki/index.md")
    index_after = index_before
    for topic, existing in zip(draft.topics, page_documents, strict=True):
        index_after = _update_index(
            index_after,
            topic,
            source_title,
            today=today,
        )
    index_change = FileChange(
        action="update",
        path="wiki/index.md",
        reason=(
            "Контроллер добавляет навигационные строки, не изменяя старый "
            "текст индекса."
        ),
        before_content=index_before,
        after_content=index_after,
    )

    log_before = workspace.read_text("wiki/log.md")
    log_after = _append_log(
        log_before,
        source_path,
        source_title,
        draft.topics,
        proposal_path,
        conflicts,
        today=today,
    )
    log_change = FileChange(
        action="update",
        path="wiki/log.md",
        reason=(
            "Контроллер формирует append-only запись с точным Proposal."
        ),
        before_content=log_before,
        after_content=log_after,
    )

    changeset = ChangeSet(
        version=1,
        source_path=source_path,
        source_sha256=source_sha256,
        summary=draft.summary,
        reading_limitations=reading_limitations,
        conflicts=_deduplicate(conflicts),
        links_added=_deduplicate(links_added),
        links_removed=[],
        verification=[
            "Проверить точный source_path карточки.",
            "Проверить сохранение всех старых строк Wiki.",
            "Проверить разрешение Wiki-ссылок.",
            "Проверить ограничения чтения и происхождение утверждений.",
            "После apply проверить актуальность FAISS.",
        ],
        changes=[
            *page_changes,
            source_change,
            index_change,
            log_change,
        ],
    )
    validate_changeset(
        workspace,
        changeset,
        expected_proposal_path=proposal_path,
    )
    return changeset


def parse_knowledge_draft(
    model_response: str,
    *,
    source_path: str,
) -> KnowledgeDraft:
    value = extract_json_object(model_response)
    protocol = value.get("protocol")
    if protocol not in {None, "knowledge-v1"}:
        raise ValidationError(
            "Неподдерживаемый протокол смыслового ingest: "
            f"{protocol!r}; ожидался knowledge-v1"
        )
    raw_topics = value.get("topics")
    if not isinstance(raw_topics, list) or not raw_topics:
        raise ValidationError(
            "Смысловой ответ ingest должен содержать непустой массив topics"
        )
    if len(raw_topics) > MAX_TOPICS:
        raise ValidationError(
            f"Смысловой ответ содержит больше {MAX_TOPICS} тем"
        )

    topics: list[KnowledgeTopic] = []
    for number, raw_topic in enumerate(raw_topics, 1):
        if not isinstance(raw_topic, dict):
            raise ValidationError(f"topics[{number}] должен быть объектом")
        title = _safe_title(
            _required_text(raw_topic, "title", f"topics[{number}]")
        )
        summary = _required_text(raw_topic, "summary", f"topics[{number}]")
        claims = _text_list(
            raw_topic.get("claims"),
            f"topics[{number}].claims",
            required=True,
            limit=MAX_CLAIMS_PER_TOPIC,
        )
        topics.append(
            KnowledgeTopic(
                title=title,
                summary=summary,
                claims=tuple(claims),
                aliases=tuple(
                    _text_list(
                        raw_topic.get("aliases", []),
                        f"topics[{number}].aliases",
                    )
                ),
                tags=tuple(
                    item.casefold()
                    for item in _text_list(
                        raw_topic.get("tags", []),
                        f"topics[{number}].tags",
                    )
                ),
                category=_category(raw_topic.get("category")),
                index_section=_optional_text(
                    raw_topic.get("index_section"),
                    default="Добавленные темы",
                    label=f"topics[{number}].index_section",
                ),
                related_titles=tuple(
                    _text_list(
                        raw_topic.get("related_topics", []),
                        f"topics[{number}].related_topics",
                    )
                ),
                limitations=tuple(
                    _text_list(
                        raw_topic.get("limitations", []),
                        f"topics[{number}].limitations",
                    )
                ),
            )
        )

    source_default = Path(source_path).stem
    source_title = _safe_title(
        _optional_text(
            value.get("source_title"),
            default=source_default,
            label="source_title",
        )
    )
    source_summary = _optional_text(
        value.get("source_summary"),
        default=f"Материал из файла {Path(source_path).name}.",
        label="source_summary",
    )
    summary = _optional_text(
        value.get("summary"),
        default=(
            "Интегрировать подтверждаемые знания источника в "
            + ", ".join(topic.title for topic in topics)
            + "."
        ),
        label="summary",
    )
    return KnowledgeDraft(
        summary=summary,
        source_title=source_title,
        source_summary=source_summary,
        source_limitations=tuple(
            _text_list(
                value.get("source_limitations", []),
                "source_limitations",
            )
        ),
        conflicts=tuple(
            _text_list(value.get("conflicts", []), "conflicts")
        ),
        topics=tuple(topics),
    )


def _render_new_page(
    topic: KnowledgeTopic,
    source_title: str,
    related: list[str],
    *,
    today: str,
) -> str:
    aliases = _yaml_list("aliases", topic.aliases)
    tags = _yaml_list("tags", topic.tags)
    claims = "\n".join(
        f"- {_claim_with_source(claim, source_title)}"
        for claim in topic.claims
    )
    relations = (
        "\n".join(
            f"- [[{title}]] — смысловая связь, указанная при ingest."
            for title in related
        )
        if related
        else "Связанные тематические страницы пока не определены."
    )
    limitations = (
        "\n".join(f"- {item}" for item in topic.limitations)
        if topic.limitations
        else "Дополнительные ограничения темы в источнике не указаны."
    )
    status = "draft" if topic.limitations else "active"
    return f"""---
title: {_yaml_scalar(topic.title)}
category: {topic.category}
{aliases}
{tags}
status: {status}
updated_at: {today}
---

# {topic.title}

## Краткое описание

{topic.summary} [[{source_title}]]

## Основная информация

{claims}

## Ограничения

{limitations}

## Связанные страницы

{relations}

## Противоречия и неопределённости

Не выявлены по использованному источнику.

## Источники

- [[{source_title}]]
"""


def _append_topic_knowledge(
    before: str,
    topic: KnowledgeTopic,
    source_title: str,
    related: list[str],
    *,
    today: str,
) -> str:
    after = _ensure_source_link(before, source_title)
    claims = "\n".join(
        f"- {_claim_with_source(claim, source_title)}"
        for claim in topic.claims
    )
    relations = "".join(
        f"\n- [[{title}]] — связь из нового источника."
        for title in related
    )
    addition = (
        f"\n\n## Дополнение {today}: {source_title}\n\n"
        f"{topic.summary} [[{source_title}]]\n\n"
        f"{claims}{relations}\n"
    )
    return _append_text(after, addition)


def _render_source_card(
    title: str,
    source_path: str,
    source_text_path: str,
    draft: KnowledgeDraft,
    limitations: list[str],
    *,
    today: str,
) -> str:
    topic_links = "\n".join(
        f"- [[{topic.title}]]" for topic in draft.topics
    )
    related = "\n".join(
        f"- [[{topic.title}]] — интегрированная тема."
        for topic in draft.topics
    )
    limitations_text = (
        "\n".join(f"- {item}" for item in limitations)
        if limitations
        else "Существенные ограничения чтения не выявлены."
    )
    status = "partial" if limitations else "processed"
    return f"""---
title: {_yaml_scalar(title)}
category: sources
source_path: {_yaml_scalar(source_path)}
status: {status}
updated_at: {today}
---

# {title}

## Краткое содержание

{draft.source_summary}

## Основные темы

{topic_links}

## Связанные страницы

{related}

## Ограничения чтения

{limitations_text}

## Текстовое представление

`{source_text_path}`

## Исходный файл

`{source_path}`
"""


def _update_index(
    content: str,
    topic: KnowledgeTopic,
    source_title: str,
    *,
    today: str,
) -> str:
    if f"[[{topic.title}]]" in content:
        line = (
            f"  - дополнено {today} по [[{source_title}]] — "
            f"{topic.summary}"
        )
        return _insert_after_link_line(content, topic.title, line)

    entry = f"- [[{topic.title}]] — {topic.summary}\n"
    section_pattern = re.compile(
        rf"^##[ \t]+{re.escape(topic.index_section)}[ \t]*$",
        re.MULTILINE,
    )
    match = section_pattern.search(content)
    if match is None:
        addition = f"\n\n## {topic.index_section}\n\n{entry}"
        return _append_text(content, addition)
    next_heading = re.search(
        r"^##[ \t]+",
        content[match.end() :],
        re.MULTILINE,
    )
    insertion = (
        match.end() + next_heading.start()
        if next_heading is not None
        else len(content)
    )
    return _insert_text(content, insertion, "\n" + entry)


def _append_log(
    before: str,
    source_path: str,
    source_title: str,
    topics: tuple[KnowledgeTopic, ...],
    proposal_path: str,
    conflicts: list[str],
    *,
    today: str,
) -> str:
    pages = ", ".join(f"[[{topic.title}]]" for topic in topics)
    conflict_text = (
        f"- конфликты и неопределённости: {len(conflicts)};\n"
        if conflicts
        else "- конфликты не выявлены;\n"
    )
    entry = (
        f"\n\n## {today}\n\n"
        f"### Ingest {Path(source_path).name}\n\n"
        f"- интегрированы темы: {pages};\n"
        f"- создана карточка [[{source_title}]];\n"
        f"{conflict_text}"
        f"- источник: `{source_path}`;\n"
        f"- применён Proposal `{proposal_path}`.\n"
    )
    return _append_text(before, entry)


def _resolve_topic(
    documents: list[WikiDocument],
    title: str,
    *,
    expected_path: str | None = None,
) -> WikiDocument | None:
    matches = [
        document
        for document in documents
        if (
            document.path.startswith("wiki/pages/")
            and (
                title.casefold()
                in {name.casefold() for name in document.names if name}
                or (
                    expected_path is not None
                    and document.path.casefold() == expected_path.casefold()
                )
            )
        )
    ]
    if len(matches) > 1:
        raise ValidationError(
            f"Тема {title!r} неоднозначно совпадает с несколькими страницами"
        )
    return matches[0] if matches else None


def _normalize_topics_for_catalog(
    topics: tuple[KnowledgeTopic, ...],
    documents: list[WikiDocument],
) -> tuple[tuple[KnowledgeTopic, ...], list[str]]:
    """Привязать темы к текущим страницам и убрать неоднозначные aliases."""

    aligned: list[KnowledgeTopic] = []
    conflicts: list[str] = []
    for topic in topics:
        expected_path = f"wiki/pages/{topic.title}.md"
        existing = _resolve_topic(
            documents,
            topic.title,
            expected_path=expected_path,
        )
        if (
            existing is not None
            and existing.title
            and existing.title.casefold() != topic.title.casefold()
        ):
            original_title = topic.title
            topic = replace(
                topic,
                title=existing.title,
                aliases=tuple(
                    _deduplicate([*topic.aliases, original_title])
                ),
            )
            conflicts.append(
                f"Тема {original_title!r} сопоставлена с существующей "
                f"страницей {existing.path}; используется её title "
                f"{existing.title!r} и действие update."
            )
        aligned.append(topic)

    proposed_titles = {
        topic.title.casefold(): index
        for index, topic in enumerate(aligned)
    }
    alias_owners: dict[str, set[int]] = {}
    for index, topic in enumerate(aligned):
        for alias in topic.aliases:
            normalized = alias.casefold()
            if normalized:
                alias_owners.setdefault(normalized, set()).add(index)

    existing_name_owners: dict[str, set[str]] = {}
    for document in documents:
        if not document.path.startswith("wiki/pages/"):
            continue
        for name in document.names:
            if name:
                existing_name_owners.setdefault(name.casefold(), set()).add(
                    document.path
                )

    normalized_topics: list[KnowledgeTopic] = []
    for index, topic in enumerate(aligned):
        target = _resolve_topic(
            documents,
            topic.title,
            expected_path=f"wiki/pages/{topic.title}.md",
        )
        target_path = target.path if target is not None else None
        kept_aliases: list[str] = []
        for alias in topic.aliases:
            normalized = alias.casefold()
            reason = ""
            title_owner = proposed_titles.get(normalized)
            if normalized == topic.title.casefold():
                reason = "alias совпадает с title той же темы"
            elif title_owner is not None and title_owner != index:
                reason = "alias совпадает с title другой темы Proposal"
            elif len(alias_owners.get(normalized, set())) > 1:
                reason = "alias указан у нескольких тем Proposal"
            else:
                owners = existing_name_owners.get(normalized, set())
                if owners and owners != ({target_path} if target_path else set()):
                    reason = "alias уже принадлежит другой странице Wiki"
            if reason:
                conflicts.append(
                    f"Alias {alias!r} не добавлен к теме {topic.title!r}: "
                    f"{reason}."
                )
                continue
            kept_aliases.append(alias)
        normalized_topics.append(
            replace(topic, aliases=tuple(_deduplicate(kept_aliases)))
        )

    return tuple(normalized_topics), _deduplicate(conflicts)


def _unique_source_title(
    requested: str,
    source_path: str,
    documents: list[WikiDocument],
    *,
    reserved_titles: set[str],
) -> str:
    existing_for_source = [
        document
        for document in documents
        if (
            document.path.startswith("wiki/sources/")
            and document.source_path == source_path
        )
    ]
    if existing_for_source:
        raise ValidationError(
            "Источник уже имеет карточку: "
            + ", ".join(item.path for item in existing_for_source)
        )
    occupied = {
        document.title.casefold() for document in documents if document.title
    }
    occupied.update(title.casefold() for title in reserved_titles)
    candidate = requested
    if candidate.casefold() in occupied:
        candidate = f"{requested} — {Path(source_path).name}"
    counter = 2
    base = candidate
    while candidate.casefold() in occupied:
        candidate = f"{base} ({counter})"
        counter += 1
    return candidate


def _ensure_source_link(content: str, source_title: str) -> str:
    link = f"[[{source_title}]]"
    heading = re.search(
        r"^##[ \t]+Источники[ \t]*$",
        content,
        re.MULTILINE | re.IGNORECASE,
    )
    if heading is None:
        return _append_text(
            content,
            f"\n\n## Источники\n\n- {link}\n",
        )
    next_heading = re.search(
        r"^##[ \t]+",
        content[heading.end() :],
        re.MULTILINE,
    )
    end = (
        heading.end() + next_heading.start()
        if next_heading is not None
        else len(content)
    )
    if link in content[heading.end() : end]:
        return content
    return _insert_text(content, end, f"\n- {link}\n")


def _insert_after_link_line(
    content: str,
    title: str,
    new_line: str,
) -> str:
    lines = content.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if f"[[{title}]]" not in line:
            continue
        insertion = sum(len(item) for item in lines[: index + 1])
        prefix = "" if line.endswith(("\n", "\r")) else "\n"
        return _insert_text(content, insertion, prefix + new_line + "\n")
    return _append_text(content, "\n" + new_line + "\n")


def _insert_text(content: str, index: int, addition: str) -> str:
    prefix = content[:index]
    suffix = content[index:]
    if prefix and not prefix.endswith(("\n", "\r")):
        addition = "\n" + addition.lstrip("\r\n")
    if suffix and not addition.endswith(("\n", "\r")):
        addition += "\n"
    return prefix + addition + suffix


def _append_text(content: str, addition: str) -> str:
    if not addition:
        return content
    separator = "" if not content or content.endswith(("\n", "\r")) else "\n"
    return content + separator + addition.lstrip("\r\n")


def _claim_with_source(claim: str, source_title: str) -> str:
    cleaned = claim.rstrip()
    punctuation = "" if cleaned.endswith((".", "!", "?", ";", ":")) else "."
    return f"{cleaned}{punctuation} [[{source_title}]]"


def _category(value: Any) -> str:
    category = str(value or "").strip()
    return category if category in PAGE_CATEGORIES else "concepts"


def _required_text(value: dict[str, Any], key: str, label: str) -> str:
    return _optional_text(
        value.get(key),
        default="",
        label=f"{label}.{key}",
        required=True,
    )


def _optional_text(
    value: Any,
    *,
    default: str,
    label: str,
    required: bool = False,
) -> str:
    text = _normalize_model_text(value) if isinstance(value, str) else ""
    if not text:
        if required:
            raise ValidationError(f"{label} должен быть непустой строкой")
        text = default
    _validate_one_line(text, label)
    return text


def _text_list(
    value: Any,
    label: str,
    *,
    required: bool = False,
    limit: int = 32,
) -> list[str]:
    if not isinstance(value, list):
        if required:
            raise ValidationError(f"{label} должен быть массивом строк")
        return []
    if len(value) > limit:
        raise ValidationError(f"{label} содержит больше {limit} элементов")
    result = []
    for number, item in enumerate(value, 1):
        if not isinstance(item, str) or not item.strip():
            raise ValidationError(
                f"{label}[{number}] должен быть непустой строкой"
            )
        text = _normalize_model_text(item)
        _validate_one_line(text, f"{label}[{number}]")
        result.append(text)
    if required and not result:
        raise ValidationError(f"{label} не должен быть пустым")
    return _deduplicate(result)


def _validate_one_line(value: str, label: str) -> None:
    if len(value) > MAX_TEXT:
        raise ValidationError(
            f"{label} превышает лимит {MAX_TEXT} символов"
        )
    if any(character in value for character in ("\n", "\r", "\x00")):
        raise ValidationError(f"{label} должен занимать одну строку")
def _safe_title(value: str) -> str:
    title = (
        value.replace("/", "／")
        .replace("\\", "＼")
        .replace("[", "(")
        .replace("]", ")")
        .replace("|", "—")
        .strip(" .")
    )
    encoded = title.encode("utf-8")
    if len(encoded) > 180:
        while len(title.encode("utf-8")) > 180:
            title = title[:-1]
        title = title.rstrip(" .")
    if not title:
        raise ValidationError("Название темы или источника стало пустым")
    return title


def _normalize_model_text(value: str) -> str:
    text = value.replace("\x00", " ")
    text = re.sub(r"\[\[([^\[\]\r\n]+)\]\]", r"\1", text)
    text = " ".join(text.split())
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT].rstrip()
    return text


def _yaml_scalar(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _yaml_list(name: str, values: tuple[str, ...]) -> str:
    if not values:
        return f"{name}: []"
    return name + ":\n" + "\n".join(
        f"  - {_yaml_scalar(value)}" for value in values
    )


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
