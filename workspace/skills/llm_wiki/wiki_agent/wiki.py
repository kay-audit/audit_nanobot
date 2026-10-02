"""Разбор Markdown-Wiki, ссылок и локальный выбор релевантного контекста."""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from .errors import ValidationError
from .models import SelectedContext
from .workspace import Workspace


WIKI_LINK_RE = re.compile(r"\[\[([^\[\]\n]+?)\]\]")
WORD_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё][0-9A-Za-zА-Яа-яЁё_-]*")
STOP_WORDS = {
    "и",
    "в",
    "во",
    "на",
    "с",
    "со",
    "к",
    "как",
    "что",
    "это",
    "из",
    "для",
    "по",
    "о",
    "об",
    "а",
    "или",
    "the",
    "a",
    "an",
    "of",
    "to",
    "and",
    "in",
    "is",
    "are",
}


@dataclass(frozen=True)
class FrontMatter:
    values: dict[str, Any]
    body: str
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class WikiDocument:
    path: str
    title: str
    category: str
    aliases: tuple[str, ...]
    tags: tuple[str, ...]
    status: str
    updated_at: str
    source_path: str | None
    content: str
    body: str
    links: tuple[str, ...]
    front_matter_errors: tuple[str, ...] = ()

    @property
    def names(self) -> tuple[str, ...]:
        return (self.title, *self.aliases)


@dataclass
class WikiCatalog:
    documents: list[WikiDocument]
    by_title: dict[str, list[WikiDocument]] = field(init=False)
    by_name: dict[str, list[WikiDocument]] = field(init=False)
    by_path: dict[str, WikiDocument] = field(init=False)

    def __post_init__(self) -> None:
        self.by_title = {}
        self.by_name = {}
        self.by_path = {}
        for document in self.documents:
            self.by_path[document.path] = document
            if document.title:
                self.by_title.setdefault(document.title, []).append(document)
            for name in document.names:
                if name:
                    self.by_name.setdefault(name.casefold(), []).append(document)

    def resolve_link(self, title: str) -> list[WikiDocument]:
        return self.by_title.get(title.strip(), [])


def parse_front_matter(content: str) -> FrontMatter:
    """Разобрать используемое в проекте безопасное подмножество YAML."""

    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return FrontMatter({}, content, ("нет открывающего `---`",))
    try:
        end = next(
            index
            for index, line in enumerate(lines[1:], 1)
            if line.strip() == "---"
        )
    except StopIteration:
        return FrontMatter({}, content, ("нет закрывающего `---`",))

    values: dict[str, Any] = {}
    errors: list[str] = []
    current_list: str | None = None
    for line_number, raw_line in enumerate(lines[1:end], 2):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if raw_line.startswith((" ", "\t")):
            stripped = raw_line.strip()
            if stripped.startswith("- ") and current_list is not None:
                item = _parse_scalar(stripped[2:].strip())
                if not isinstance(values.get(current_list), list):
                    values[current_list] = []
                values[current_list].append(str(item))
            else:
                errors.append(
                    f"строка {line_number}: неподдерживаемый вложенный YAML"
                )
            continue
        if ":" not in raw_line:
            errors.append(f"строка {line_number}: ожидается `ключ: значение`")
            current_list = None
            continue
        key, raw_value = raw_line.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if not key:
            errors.append(f"строка {line_number}: пустой ключ")
            current_list = None
            continue
        if key in values:
            errors.append(f"строка {line_number}: повтор поля `{key}`")
        if raw_value == "":
            values[key] = []
            current_list = key
        else:
            values[key] = _parse_scalar(raw_value)
            current_list = None

    body = "\n".join(lines[end + 1 :]).lstrip("\n")
    return FrontMatter(values, body, tuple(errors))


def load_catalog(
    workspace: Workspace,
    *,
    overrides: dict[str, str] | None = None,
) -> WikiCatalog:
    overrides = overrides or {}
    paths = set(workspace.list_markdown("wiki/pages"))
    paths.update(workspace.list_markdown("wiki/sources"))
    paths.update(
        path
        for path in overrides
        if path.startswith(("wiki/pages/", "wiki/sources/"))
        and path.endswith(".md")
    )
    documents: list[WikiDocument] = []
    for path in sorted(paths):
        if path in overrides:
            content = overrides[path]
        else:
            content = workspace.read_text(path)
        documents.append(document_from_content(path, content))
    return WikiCatalog(documents)


def document_from_content(path: str, content: str) -> WikiDocument:
    front = parse_front_matter(content)
    aliases = _as_string_tuple(front.values.get("aliases"))
    tags = _as_string_tuple(front.values.get("tags"))
    source_path = front.values.get("source_path")
    return WikiDocument(
        path=path,
        title=str(front.values.get("title", "")).strip(),
        category=str(front.values.get("category", "")).strip(),
        aliases=aliases,
        tags=tags,
        status=str(front.values.get("status", "")).strip(),
        updated_at=str(front.values.get("updated_at", "")).strip(),
        source_path=str(source_path).strip() if source_path else None,
        content=content,
        body=front.body,
        links=tuple(extract_wiki_links(front.body)),
        front_matter_errors=front.errors,
    )


def extract_wiki_links(content: str) -> list[str]:
    return [match.group(1).strip() for match in WIKI_LINK_RE.finditer(content)]


def tokenize(value: str) -> set[str]:
    return {
        word.casefold()
        for word in WORD_RE.findall(value)
        if len(word) > 1 and word.casefold() not in STOP_WORDS
    }


def relevance(document: WikiDocument, terms: set[str]) -> int:
    if not terms:
        return 0
    names = tokenize(" ".join(document.names))
    tags = tokenize(" ".join(document.tags))
    body = tokenize(document.body[:20_000])
    return (
        12 * len(terms & names)
        + 5 * len(terms & tags)
        + len(terms & body)
    )


def select_query_context(
    workspace: Workspace,
    question: str,
    *,
    max_pages: int,
    max_hops: int,
    max_context_chars: int,
    semantic_paths: Sequence[str] | None = None,
) -> SelectedContext:
    """Выбрать страницы поиском и расширить контекст по Wiki-ссылкам."""

    catalog = load_catalog(workspace)
    index_content = workspace.read_text("wiki/index.md")
    terms = tokenize(question)

    all_pages = [
        document
        for document in catalog.documents
        if document.path.startswith("wiki/pages/")
    ]
    semantic_mode = semantic_paths is not None
    if semantic_mode:
        by_path = {document.path: document for document in all_pages}
        seeds = [
            by_path[path]
            for path in semantic_paths
            if path in by_path
        ]
    else:
        reachable_titles = _reachable_titles(
            catalog, index_content, max_hops=max_hops
        )
        pages = [
            document
            for document in all_pages
            if document.title in reachable_titles
        ]
        index_targets = set(extract_wiki_links(index_content))
        ranked = sorted(
            pages,
            key=lambda item: (
                relevance(item, terms),
                item.title in index_targets,
                item.title,
            ),
            reverse=True,
        )
        seeds = [item for item in ranked if relevance(item, terms) > 0]
        if not seeds:
            seeds = [
                item for item in ranked if item.title in index_targets
            ][: min(3, max_pages)]
    selected_pages: list[WikiDocument] = seeds[:max_pages]
    selected_titles = {item.title for item in selected_pages}

    frontier = list(selected_pages)
    for _ in range(max_hops):
        next_frontier: list[WikiDocument] = []
        for document in frontier:
            for link in document.links:
                targets = catalog.resolve_link(link)
                if len(targets) != 1:
                    continue
                target = targets[0]
                if not target.path.startswith("wiki/pages/"):
                    continue
                if (
                    not semantic_mode
                    and target.title not in reachable_titles
                ):
                    continue
                if target.title in selected_titles:
                    continue
                if relevance(target, terms) <= 0 and len(selected_pages) >= 3:
                    continue
                selected_pages.append(target)
                selected_titles.add(target.title)
                next_frontier.append(target)
                if len(selected_pages) >= max_pages:
                    break
            if len(selected_pages) >= max_pages:
                break
        frontier = next_frontier
        if not frontier or len(selected_pages) >= max_pages:
            break

    selected_sources: list[WikiDocument] = []
    selected_source_titles: set[str] = set()
    for page in selected_pages:
        for link in page.links:
            targets = catalog.resolve_link(link)
            if len(targets) != 1:
                continue
            target = targets[0]
            if target.category != "sources":
                continue
            if target.title in selected_source_titles:
                continue
            selected_sources.append(target)
            selected_source_titles.add(target.title)

    paths: list[str] = ["wiki/index.md"]
    sections: list[tuple[str, str]] = [("wiki/index.md", index_content)]
    intrinsic_truncated: set[str] = set()
    for document in selected_pages:
        paths.append(document.path)
        sections.append((document.path, document.content))
    for document in selected_sources:
        paths.append(document.path)
        sections.append((document.path, document.content))
        excerpt_path, excerpt, excerpt_partial = _source_excerpt(
            workspace, document, question, max_chars=5_000
        )
        if excerpt_path and excerpt:
            paths.append(excerpt_path)
            sections.append((excerpt_path, excerpt))
            if excerpt_partial:
                intrinsic_truncated.add(excerpt_path)

    (
        packed,
        included_paths,
        truncated_paths,
        omitted_paths,
    ) = pack_sections(sections, max_context_chars)
    return SelectedContext(
        paths=tuple(dict.fromkeys(included_paths)),
        text=packed,
        truncated_paths=tuple(
            dict.fromkeys(
                [
                    *truncated_paths,
                    *(
                        path
                        for path in included_paths
                        if path in intrinsic_truncated
                    ),
                ]
            )
        ),
        omitted_paths=tuple(dict.fromkeys(omitted_paths)),
    )


def select_ingest_context(
    workspace: Workspace,
    source_text: str,
    *,
    max_pages: int,
    max_context_chars: int,
) -> SelectedContext:
    """Выбрать существующие страницы, близкие к новому источнику."""

    catalog = load_catalog(workspace)
    index_content = workspace.read_text("wiki/index.md")
    log_content = workspace.read_text("wiki/log.md")
    terms = tokenize(source_text[:40_000])
    pages = [
        document
        for document in catalog.documents
        if document.path.startswith("wiki/pages/")
    ]
    ranked_pages = sorted(
        pages,
        key=lambda item: (relevance(item, terms), item.title),
        reverse=True,
    )
    selected_pages = ranked_pages[:max_pages]
    source_cards = [
        document
        for document in catalog.documents
        if document.category == "sources"
    ]

    sections: list[tuple[str, str]] = [
        ("wiki/index.md", index_content),
        ("wiki/log.md", log_content),
    ]
    required_size = sum(
        len(f"\n\n===== FILE: {path} =====\n\n") + len(content)
        for path, content in sections
    )
    if required_size > max_context_chars:
        raise ValidationError(
            "Лимит ingest-контекста слишком мал для полного index и log: "
            f"нужно минимум {required_size}, доступно {max_context_chars}. "
            "Увеличьте LLM_WIKI_MAX_CONTEXT_CHARS."
        )
    for document in selected_pages:
        sections.append((document.path, document.content))
    for document in source_cards:
        sections.append((document.path, document.content))

    (
        packed,
        included_paths,
        truncated_paths,
        omitted_paths,
    ) = pack_sections(
        sections,
        max_context_chars,
        allow_partial=False,
    )
    return SelectedContext(
        paths=tuple(dict.fromkeys(included_paths)),
        text=packed,
        truncated_paths=tuple(dict.fromkeys(truncated_paths)),
        omitted_paths=tuple(dict.fromkeys(omitted_paths)),
    )


def _source_excerpt(
    workspace: Workspace,
    card: WikiDocument,
    question: str,
    *,
    max_chars: int,
) -> tuple[str | None, str | None, bool]:
    if not card.source_path:
        return None, None, False
    try:
        text_path = workspace.source_text_path(card.source_path)
        content = workspace.read_text(text_path)
    except ValidationError:
        return None, None, False
    excerpt = relevant_excerpt(content, question, max_chars=max_chars)
    partial = len(excerpt) < len(content)
    if partial:
        excerpt += (
            f"\n\n[PARTIAL SOURCE EXCERPT {text_path}: selected "
            f"{len(excerpt)} of {len(content)} chars]"
        )
    return text_path, excerpt, partial


def relevant_excerpt(content: str, query: str, *, max_chars: int) -> str:
    terms = tokenize(query)
    blocks = [
        block.strip()
        for block in re.split(r"\n\s*\n", content)
        if block.strip()
    ]
    ranked = sorted(
        enumerate(blocks),
        key=lambda item: (
            len(terms & tokenize(item[1])),
            -item[0],
        ),
        reverse=True,
    )
    chosen: list[tuple[int, str]] = []
    total = 0
    for index, block in ranked:
        if terms and not (terms & tokenize(block)) and chosen:
            continue
        remaining = max_chars - total
        if remaining <= 0:
            break
        piece = block[:remaining]
        chosen.append((index, piece))
        total += len(piece) + 2
    if not chosen and blocks:
        chosen = [(0, blocks[0][:max_chars])]
    return "\n\n".join(block for _, block in sorted(chosen))


def pack_sections(
    sections: Iterable[tuple[str, str]],
    max_chars: int,
    *,
    allow_partial: bool = True,
) -> tuple[str, list[str], list[str], list[str]]:
    section_list = list(sections)
    parts: list[str] = []
    included: list[str] = []
    truncated: list[str] = []
    omitted: list[str] = []
    used = 0
    for index, (path, content) in enumerate(section_list):
        header = f"\n\n===== FILE: {path} =====\n\n"
        remaining = max_chars - used - len(header)
        if remaining <= 0:
            omitted.extend(item[0] for item in section_list[index:])
            break
        if len(content) <= remaining:
            piece = content
        elif not allow_partial:
            omitted.extend(item[0] for item in section_list[index:])
            break
        else:
            sent = remaining
            marker = ""
            for _ in range(3):
                marker = (
                    f"\n\n[TRUNCATED {path}: sent {sent} of "
                    f"{len(content)} chars]"
                )
                sent = max(0, remaining - len(marker))
            if len(marker) > remaining:
                omitted.extend(item[0] for item in section_list[index:])
                break
            piece = content[:sent] + marker
            truncated.append(path)
        parts.append(header + piece)
        included.append(path)
        used += len(header) + len(piece)
        if len(content) > remaining:
            omitted.extend(item[0] for item in section_list[index + 1 :])
            break
    return "".join(parts).lstrip(), included, truncated, omitted


def _reachable_titles(
    catalog: WikiCatalog,
    index_content: str,
    *,
    max_hops: int,
) -> set[str]:
    """Разрешить query только графом, начинающимся в индексе."""

    reachable: set[str] = set()
    queue: deque[tuple[str, int]] = deque(
        (link, 1) for link in extract_wiki_links(index_content)
    )
    visited_depth: dict[str, int] = {}
    while queue:
        title, depth = queue.popleft()
        if depth > max_hops:
            continue
        previous = visited_depth.get(title)
        if previous is not None and previous <= depth:
            continue
        visited_depth[title] = depth
        targets = catalog.resolve_link(title)
        if len(targets) != 1:
            continue
        document = targets[0]
        reachable.add(document.title)
        if depth < max_hops:
            queue.extend((link, depth + 1) for link in document.links)
    return reachable


def _parse_scalar(value: str) -> Any:
    if value == "[]":
        return []
    if value in {"null", "Null", "NULL", "~"}:
        return None
    if (
        len(value) >= 2
        and value[0] == value[-1]
        and value[0] in {"'", '"'}
    ):
        return value[1:-1]
    return value


def _as_string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, list):
        return tuple(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, str) and value.strip():
        return (value.strip(),)
    return ()
