"""Детерминированные проверки Wiki без участия LLM."""

from __future__ import annotations

import re
from collections import Counter, defaultdict, deque
from datetime import date
from pathlib import Path

from .models import LintIssue
from .wiki import WikiCatalog, extract_wiki_links, load_catalog
from .workspace import Workspace


PAGE_CATEGORIES = {
    "concepts",
    "entities",
    "technologies",
    "models",
    "infrastructure",
    "launch_methods",
    "use_cases",
    "guides",
    "requirements",
    "risks",
}
PAGE_STATUSES = {"active", "draft", "deprecated"}
SOURCE_STATUSES = {"unprocessed", "partial", "processed", "superseded"}
PROPOSAL_PATH_RE = re.compile(r"`(proposals/[^`\n]+\.md)`")


def run_checks(
    workspace: Workspace,
    *,
    overrides: dict[str, str] | None = None,
    known_proposals: set[str] | None = None,
) -> list[LintIssue]:
    """Проверить фактическое или виртуальное состояние Wiki."""

    overrides = overrides or {}
    catalog = load_catalog(workspace, overrides=overrides)
    issues: list[LintIssue] = []

    _check_front_matter(catalog, issues)
    _check_names(catalog, issues)
    _check_links(workspace, catalog, overrides, issues)
    _check_sources(workspace, catalog, issues)
    _check_navigation(workspace, catalog, overrides, issues)
    _check_small_sink_cycles(catalog, issues)
    _check_log_proposals(
        workspace,
        overrides,
        known_proposals or set(),
        issues,
    )
    return issues


def _check_front_matter(
    catalog: WikiCatalog, issues: list[LintIssue]
) -> None:
    for document in catalog.documents:
        for error in document.front_matter_errors:
            issues.append(
                _issue(
                    "error",
                    document.path,
                    "Некорректный front matter",
                    error,
                    "метаданные страницы нельзя надёжно обработать",
                    "исправить YAML front matter через Proposal",
                    "нет",
                )
            )

        is_source = document.path.startswith("wiki/sources/")
        required = (
            ("title", document.title),
            ("category", document.category),
            ("status", document.status),
        )
        for field, value in required:
            if not value:
                issues.append(
                    _issue(
                        "error",
                        document.path,
                        f"Отсутствует поле `{field}`",
                        "обязательное поле пусто",
                        "страница не соответствует таксономии",
                        f"добавить `{field}` через Proposal",
                        "нет",
                    )
                )

        if is_source:
            if document.category != "sources":
                issues.append(
                    _issue(
                        "error",
                        document.path,
                        "Некорректная категория карточки",
                        f"category: {document.category or '<пусто>'}",
                        "карточка не распознаётся как источник",
                        "установить category: sources",
                        "нет",
                    )
                )
            if document.status and document.status not in SOURCE_STATUSES:
                issues.append(
                    _issue(
                        "error",
                        document.path,
                        "Недопустимый статус карточки",
                        f"status: {document.status}",
                        "статус отсутствует в таксономии",
                        "выбрать допустимый статус источника",
                        "нет",
                    )
                )
        else:
            if document.category and document.category not in PAGE_CATEGORIES:
                issues.append(
                    _issue(
                        "error",
                        document.path,
                        "Недопустимая категория страницы",
                        f"category: {document.category}",
                        "категория отсутствует в таксономии",
                        "выбрать категорию из schema/taxonomy.md",
                        "нет",
                    )
                )
            if document.status and document.status not in PAGE_STATUSES:
                issues.append(
                    _issue(
                        "error",
                        document.path,
                        "Недопустимый статус страницы",
                        f"status: {document.status}",
                        "статус отсутствует в таксономии",
                        "выбрать active, draft или deprecated",
                        "нет",
                    )
                )
            source_section = _markdown_section(
                document.body,
                "Источники",
            )
            source_targets = []
            for link in extract_wiki_links(source_section):
                targets = catalog.resolve_link(link)
                if len(targets) == 1 and targets[0].category == "sources":
                    source_targets.append(targets[0])
            if not source_section.strip() or not source_targets:
                issues.append(
                    _issue(
                        "error",
                        document.path,
                        "Нет карточки в разделе источников",
                        "раздел `## Источники` отсутствует, пуст или не "
                        "содержит однозначную ссылку на карточку",
                        "значимые утверждения не имеют обязательной трассировки",
                        "добавить раздел и карточки источников через Proposal",
                        "да — проверить происхождение утверждений",
                    )
                )

        if not _valid_date(document.updated_at):
            issues.append(
                _issue(
                    "error",
                    document.path,
                    "Некорректная дата updated_at",
                    document.updated_at or "поле отсутствует во front matter",
                    "невозможно определить дату содержательного изменения",
                    "указать дату YYYY-MM-DD",
                    "нет",
                )
            )

        filename = Path(document.path).stem
        if document.title and filename != document.title:
            issues.append(
                _issue(
                    "warning",
                    document.path,
                    "Имя файла не совпадает с title",
                    f"файл: {filename}; title: {document.title}",
                    "навигация и ручной поиск становятся неоднозначными",
                    "согласовать переименование отдельным Proposal",
                    "да — учесть все входящие ссылки",
                )
            )


def _check_names(catalog: WikiCatalog, issues: list[LintIssue]) -> None:
    for title, documents in catalog.by_title.items():
        if title and len(documents) > 1:
            paths = ", ".join(item.path for item in documents)
            issues.append(
                _issue(
                    "error",
                    paths,
                    "Дублирующийся title",
                    title,
                    "Wiki-ссылка разрешается неоднозначно",
                    "разделить сущности или переименовать через Proposal",
                    "да",
                )
            )

    owners: dict[str, list[str]] = defaultdict(list)
    display: dict[str, str] = {}
    for document in catalog.documents:
        for name in document.names:
            normalized = name.casefold()
            if normalized:
                owners[normalized].append(document.path)
                display[normalized] = name
    for normalized, paths in owners.items():
        unique_paths = sorted(set(paths))
        if len(unique_paths) > 1:
            issues.append(
                _issue(
                    "error",
                    ", ".join(unique_paths),
                    "Конфликт title или alias",
                    display[normalized],
                    "поиск по альтернативному названию неоднозначен",
                    "оставить имя только у одной сущности",
                    "да",
                )
            )


def _check_links(
    workspace: Workspace,
    catalog: WikiCatalog,
    overrides: dict[str, str],
    issues: list[LintIssue],
) -> None:
    contents = {
        document.path: document.content for document in catalog.documents
    }
    for special in ("wiki/index.md", "wiki/log.md"):
        contents[special] = overrides.get(
            special, workspace.read_text(special)
        )

    for path, content in contents.items():
        for link in extract_wiki_links(content):
            targets = catalog.resolve_link(link)
            if not targets:
                issues.append(
                    _issue(
                        "error",
                        path,
                        "Битая Wiki-ссылка",
                        f"[[{link}]]",
                        "переход ведёт на несуществующую страницу",
                        "создать цель в том же Proposal или исправить ссылку",
                        "нет",
                    )
                )
            elif len(targets) > 1:
                issues.append(
                    _issue(
                        "error",
                        path,
                        "Неоднозначная Wiki-ссылка",
                        f"[[{link}]] имеет {len(targets)} целей",
                        "агент не может выбрать единственную страницу",
                        "устранить дубли title",
                        "да",
                    )
                )
            elif targets[0].path == path:
                issues.append(
                    _issue(
                        "warning",
                        path,
                        "Ссылка страницы на себя",
                        f"[[{link}]]",
                        "связь не улучшает навигацию",
                        "удалить ссылку, если она не обоснована",
                        "да",
                    )
                )


def _check_sources(
    workspace: Workspace,
    catalog: WikiCatalog,
    issues: list[LintIssue],
) -> None:
    source_path_owners: dict[str, list[str]] = defaultdict(list)
    for document in catalog.documents:
        if document.category != "sources":
            continue
        if not document.source_path:
            issues.append(
                _issue(
                    "error",
                    document.path,
                    "Нет source_path",
                    "карточка не указывает оригинал",
                    "происхождение знаний невозможно проверить",
                    "указать существующий файл в raw/sources/",
                    "нет",
                )
            )
            continue
        source_path_owners[document.source_path].append(document.path)
        try:
            workspace.source_path(document.source_path)
        except Exception as exc:
            issues.append(
                _issue(
                    "error",
                    document.path,
                    "Некорректный source_path",
                    str(exc),
                    "карточка не ведёт к неизменяемому оригиналу",
                    "исправить путь на существующий raw/sources/*",
                    "нет",
                )
            )
    for source_path, owners in source_path_owners.items():
        if len(owners) > 1:
            issues.append(
                _issue(
                    "error",
                    ", ".join(sorted(owners)),
                    "Несколько карточек одного первоисточника",
                    source_path,
                    "происхождение и статус обработки становятся "
                    "неоднозначными",
                    "оставить одну карточку через отдельный Proposal",
                    "да — проверить, не являются ли файлы разными версиями",
                )
            )


def _check_navigation(
    workspace: Workspace,
    catalog: WikiCatalog,
    overrides: dict[str, str],
    issues: list[LintIssue],
) -> None:
    index_content = overrides.get(
        "wiki/index.md", workspace.read_text("wiki/index.md")
    )
    reachable: set[str] = set()
    queue: deque[str] = deque(extract_wiki_links(index_content))
    while queue:
        title = queue.popleft()
        if title in reachable:
            continue
        targets = catalog.resolve_link(title)
        if len(targets) != 1:
            continue
        document = targets[0]
        reachable.add(document.title)
        queue.extend(document.links)

    for document in catalog.documents:
        if document.title and document.title not in reachable:
            issues.append(
                _issue(
                    "warning",
                    document.path,
                    "Страница недостижима из индекса",
                    document.title,
                    "query, начинающийся с индекса, может не найти страницу",
                    "добавить осмысленный маршрут из индексируемой страницы",
                    "да",
                )
            )
        if not document.body.strip():
            issues.append(
                _issue(
                    "error",
                    document.path,
                    "Пустая страница",
                    "после front matter нет содержимого",
                    "страница не содержит полезных знаний",
                    "заполнить или отдельно согласовать удаление",
                    "да",
                )
            )

    incoming = Counter()
    outgoing = Counter()
    for document in catalog.documents:
        for link in document.links:
            targets = catalog.resolve_link(link)
            if len(targets) == 1:
                outgoing[document.title] += 1
                incoming[targets[0].title] += 1
    for document in catalog.documents:
        if incoming[document.title] == 0 and outgoing[document.title] == 0:
            issues.append(
                _issue(
                    "warning",
                    document.path,
                    "Изолированная страница",
                    "нет входящих и исходящих Wiki-связей",
                    "страница не участвует в карте знаний",
                    "добавить только полезные смысловые связи",
                    "да",
                )
            )


def _check_log_proposals(
    workspace: Workspace,
    overrides: dict[str, str],
    known_proposals: set[str],
    issues: list[LintIssue],
) -> None:
    log_content = overrides.get("wiki/log.md", workspace.read_text("wiki/log.md"))
    for proposal_path in PROPOSAL_PATH_RE.findall(log_content):
        if proposal_path in known_proposals:
            continue
        try:
            workspace.resolve(
                proposal_path,
                must_exist=True,
                allowed_roots=("proposals",),
            )
        except Exception as exc:
            issues.append(
                _issue(
                    "error",
                    "wiki/log.md",
                    "Журнал ссылается на отсутствующий Proposal",
                    str(exc),
                    "историю изменения нельзя проверить",
                    "восстановить Proposal или уточнить запись через Proposal",
                    "да",
                )
            )


def _check_small_sink_cycles(
    catalog: WikiCatalog,
    issues: list[LintIssue],
) -> None:
    """Найти небольшие циклы тематических страниц без выхода."""

    pages = {
        document.title: document
        for document in catalog.documents
        if document.path.startswith("wiki/pages/") and document.title
    }
    graph: dict[str, set[str]] = {}
    for title, document in pages.items():
        graph[title] = {
            link
            for link in document.links
            if link in pages and link != title
        }

    for component in _strongly_connected_components(graph):
        if not 2 <= len(component) <= 3:
            continue
        if any(
            target not in component
            for title in component
            for target in graph[title]
        ):
            continue
        issues.append(
            _issue(
                "warning",
                ", ".join(sorted(pages[title].path for title in component)),
                "Небольшой цикл тематических ссылок без выхода",
                " → ".join(sorted(component)),
                "узкий обход может зациклиться без перехода к соседней теме",
                "добавить только обоснованную связь наружу или оставить с "
                "ручным объяснением",
                "да — цикл может быть намеренным",
            )
        )


def _strongly_connected_components(
    graph: dict[str, set[str]],
) -> list[set[str]]:
    index = 0
    indexes: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    result: list[set[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indexes[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for target in graph[node]:
            if target not in indexes:
                visit(target)
                lowlinks[node] = min(lowlinks[node], lowlinks[target])
            elif target in on_stack:
                lowlinks[node] = min(lowlinks[node], indexes[target])
        if lowlinks[node] != indexes[node]:
            return
        component: set[str] = set()
        while stack:
            member = stack.pop()
            on_stack.remove(member)
            component.add(member)
            if member == node:
                break
        result.append(component)

    for node in graph:
        if node not in indexes:
            visit(node)
    return result


def issue_counts(issues: list[LintIssue]) -> dict[str, int]:
    counter = Counter(issue.level for issue in issues)
    return {
        "error": counter["error"],
        "warning": counter["warning"],
        "info": counter["info"],
    }


def _issue(
    level: str,
    path: str,
    title: str,
    evidence: str,
    impact: str,
    action: str,
    manual_review: str,
) -> LintIssue:
    return LintIssue(
        level=level,  # type: ignore[arg-type]
        path=path,
        title=title,
        description=title,
        evidence=evidence[:500],
        impact=impact,
        action=action,
        manual_review=manual_review,
    )


def _valid_date(value: str) -> bool:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _markdown_section(content: str, heading: str) -> str:
    match = re.search(
        rf"^##[ \t]+{re.escape(heading)}[ \t]*$",
        content,
        re.MULTILINE,
    )
    if match is None:
        return ""
    remainder = content[match.end() :]
    next_heading = re.search(r"^##[ \t]+", remainder, re.MULTILINE)
    return (
        remainder[: next_heading.start()]
        if next_heading is not None
        else remainder
    )
