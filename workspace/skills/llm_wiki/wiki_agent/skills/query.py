"""Read-only query skill."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..config import Settings
from ..errors import ValidationError
from ..jira_confluence import (
    CARD_PATH_PREFIX,
    card_documents,
    extract_evidence,
    merge_contexts,
    select_evidence_cards,
)
from ..prompts import query_request
from ..provider import LLMProvider
from ..semantic import FaissPageIndex, SemanticHit
from ..wiki import load_catalog, select_query_context
from ..workspace import Workspace


@dataclass(frozen=True)
class QueryRunResult:
    answer: str | None
    selected_paths: tuple[str, ...]
    dry_run: bool
    semantic_hits: tuple[SemanticHit, ...] = ()
    answer_path: str | None = None


def run_query(
    workspace: Workspace,
    settings: Settings,
    provider: LLMProvider,
    question: str,
    *,
    dry_run: bool = False,
    save_markdown: bool = False,
    report_mode: str = "wiki",
    semantic_index: FaissPageIndex | None = None,
) -> QueryRunResult:
    normalized = question.strip()
    if not normalized:
        raise ValidationError("Вопрос query не может быть пустым")

    sources_before = workspace.snapshot_sources()
    semantic_paths: list[str] | None = None
    hits: list[SemanticHit] = []
    if settings.query_search == "faiss":
        catalog = load_catalog(workspace)
        pages = [
            document
            for document in catalog.documents
            if document.path.startswith("wiki/pages/")
        ]
        pages.extend(card_documents(workspace))
        search_index = semantic_index or FaissPageIndex(
            workspace_root=settings.root,
            cache_dir=settings.faiss_cache_dir,
            model_cache_dir=settings.embedding_cache_dir,
            vector_cache_dir=settings.embedding_vector_cache_dir,
            model_name=settings.embedding_model,
        )
        hits = search_index.search(
            pages,
            normalized,
            limit=min(settings.faiss_top_k, settings.max_query_pages),
            min_score=settings.faiss_min_score,
        )
        semantic_paths = [
            hit.path
            for hit in hits
            if hit.path.startswith("wiki/pages/")
        ]

    context = select_query_context(
        workspace,
        normalized,
        max_pages=settings.max_query_pages,
        max_hops=settings.max_hops,
        max_context_chars=settings.max_context_chars,
        semantic_paths=semantic_paths,
    )
    card_seed_paths = [
        hit.path
        for hit in hits
        if hit.path.startswith(CARD_PATH_PREFIX)
    ]
    selection = select_evidence_cards(
        workspace,
        normalized,
        card_seed_paths,
    )
    if dry_run:
        workspace.assert_sources_unchanged(sources_before)
        return QueryRunResult(
            None,
            tuple(dict.fromkeys([*context.paths, *selection.paths])),
            True,
            tuple(hits),
        )

    if selection.cards:
        evidence = extract_evidence(
            workspace,
            provider,
            normalized,
            selection,
            max_document_chars=settings.max_file_chars,
            max_context_chars=settings.max_context_chars,
        )
        context = merge_contexts(
            (context, evidence),
            max_context_chars=settings.max_context_chars,
        )

    request = query_request(workspace, normalized, context)
    response = provider.complete(request)
    answer_path = None
    if save_markdown:
        answer_path = _save_answer_report(
            workspace,
            normalized,
            response.content,
            context.paths,
            hits,
            mode=report_mode,
        )
    workspace.assert_sources_unchanged(sources_before)
    return QueryRunResult(
        response.content,
        context.paths,
        False,
        tuple(hits),
        answer_path,
    )


def _save_answer_report(
    workspace: Workspace,
    question: str,
    answer: str,
    selected_paths: tuple[str, ...],
    hits: list[SemanticHit],
    *,
    mode: str,
) -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S-%f")
    relative = f"reports/query/{timestamp}-{mode}.md"
    hit_lines = [
        f"- `{hit.path}` — score `{hit.score:.4f}`"
        for hit in hits
    ] or ["- Нет."]
    selected_lines = [f"- `{path}`" for path in selected_paths] or ["- Нет."]
    content = "\n".join(
        [
            f"# Ответ LLM-Wiki ({mode})",
            "",
            "## Вопрос",
            "",
            question,
            "",
            "## Ответ",
            "",
            answer.strip(),
            "",
            "## Найдено FAISS",
            "",
            *hit_lines,
            "",
            "## Передано в контекст",
            "",
            *selected_lines,
            "",
        ]
    )
    workspace.write_text(
        relative,
        content,
        allowed_roots=("reports/query",),
        must_not_exist=True,
    )
    return relative
