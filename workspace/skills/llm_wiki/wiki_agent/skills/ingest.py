"""Двухфазный ingest: LLM выделяет знания, контроллер создаёт ChangeSet."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import Settings
from ..errors import ValidationError
from ..extraction import ExtractionError, extract_document, render
from ..knowledge import changeset_from_knowledge_model
from ..prompts import (
    ingest_knowledge_repair_request,
    ingest_request,
)
from ..proposal import (
    SOURCE_EXTRACTION_PREFIX,
    SOURCE_TRUNCATED_PREFIX,
    proposal_path_for_source,
    render_proposal,
    save_proposal,
)
from ..provider import LLMProvider
from ..wiki import parse_front_matter, select_ingest_context
from ..workspace import Workspace, sha256_file


MAX_REPAIR_ATTEMPTS = 2


@dataclass(frozen=True)
class IngestResult:
    proposal_path: str
    selected_paths: tuple[str, ...]
    extracted_path: str | None = None


def run_ingest(
    workspace: Workspace,
    settings: Settings,
    provider: LLMProvider,
    source_path: str,
    *,
    user_request: str = "",
) -> IngestResult:
    sources_before = workspace.snapshot_sources()
    source = workspace.source_path(source_path)
    normalized_source = workspace.relative(source)
    source_sha256 = sources_before[normalized_source]
    text_path, extracted_created = _prepare_source_text(
        workspace,
        source,
        normalized_source,
        source_sha256,
        sources_before,
    )
    source_text = workspace.read_text(text_path)
    workspace.assert_sources_unchanged(sources_before)

    source_budget = max(
        1,
        min(
            int(settings.max_context_chars * 0.65),
            settings.max_file_chars,
        ),
    )
    source_payload = source_text[:source_budget]
    wiki_budget = max(
        settings.max_context_chars - len(source_payload),
        1,
    )
    source_truncated = len(source_payload) < len(source_text)
    source_limitations = _extraction_limitations(source_text, text_path)
    if source_truncated:
        source_limitations.append(
            f"{SOURCE_TRUNCATED_PREFIX} {len(source_payload)} из "
            f"{len(source_text)} символов. Непрочитанный остаток не "
            "интегрирован; карточка имеет status: partial."
        )

    context = select_ingest_context(
        workspace,
        source_payload,
        max_pages=settings.max_query_pages,
        max_context_chars=wiki_budget,
    )
    controller_limitations = list(source_limitations)
    if context.omitted_paths:
        controller_limitations.append(
            "Из-за лимита контекста не переданы существующие Wiki-файлы: "
            + ", ".join(context.omitted_paths)
            + ". Их содержимое не использовалось при анализе."
        )
    proposal_path = proposal_path_for_source(
        workspace, normalized_source
    )
    request = ingest_request(
        workspace,
        normalized_source,
        text_path,
        source_payload,
        context,
        user_request,
        proposal_path=proposal_path,
        source_total_chars=len(source_text),
        source_truncated=source_truncated,
        required_card_limitations=tuple(source_limitations),
    )
    response = provider.complete(request)
    workspace.assert_sources_unchanged(sources_before)
    repair_attempt = 0
    while True:
        try:
            changeset = changeset_from_knowledge_model(
                workspace,
                normalized_source,
                text_path,
                response.content,
                source_sha256=source_sha256,
                proposal_path=proposal_path,
                controller_limitations=controller_limitations,
            )
            break
        except ValidationError as exc:
            if repair_attempt >= MAX_REPAIR_ATTEMPTS:
                raise
            repair_attempt += 1
            workspace.assert_sources_unchanged(sources_before)
            repair_request = ingest_knowledge_repair_request(
                request,
                response.content,
                str(exc),
                attempt=repair_attempt,
                max_attempts=MAX_REPAIR_ATTEMPTS,
            )
            response = provider.complete(repair_request)
            workspace.assert_sources_unchanged(sources_before)
    workspace.assert_sources_unchanged(sources_before)
    proposal_path = save_proposal(
        workspace,
        changeset,
        proposal_path,
    )
    expected_proposal = render_proposal(changeset)
    try:
        workspace.assert_sources_unchanged(sources_before)
    except Exception:
        candidate = workspace.resolve(
            proposal_path,
            allowed_roots=("proposals",),
        )
        if (
            candidate.is_file()
            and candidate.read_text(encoding="utf-8") == expected_proposal
        ):
            candidate.unlink()
        raise
    return IngestResult(
        proposal_path,
        context.paths,
        text_path if extracted_created else None,
    )


def _prepare_source_text(
    workspace: Workspace,
    source: Path,
    source_relative: str,
    source_sha256: str,
    sources_before: dict[str, str],
) -> tuple[str, bool]:
    """Создать отсутствующую extracted-копию без перезаписи и гонок."""

    if source.suffix.lower() in {".md", ".markdown", ".txt"}:
        return workspace.source_text_path(source_relative), False

    target_relative = f"raw/extracted/{source.name}.md"
    target = workspace.resolve(
        target_relative,
        allowed_roots=("raw/extracted",),
    )
    if target.exists():
        return workspace.source_text_path(source_relative), False

    try:
        extraction = extract_document(source)
    except (ExtractionError, OSError) as exc:
        raise ValidationError(
            f"Автоматическое извлечение {source_relative} не удалось: {exc}"
        ) from exc

    content = render(
        source,
        extraction,
        original_path=source_relative,
        original_sha256=source_sha256,
    )
    workspace.assert_sources_unchanged(sources_before)
    workspace.write_text(
        target_relative,
        content,
        allowed_roots=("raw/extracted",),
        must_not_exist=True,
    )
    try:
        workspace.assert_sources_unchanged(sources_before)
    except Exception:
        if target.is_file() and target.read_text(encoding="utf-8") == content:
            target.unlink()
        raise
    return workspace.source_text_path(source_relative), True


def _extraction_limitations(
    source_text: str,
    text_path: str,
) -> list[str]:
    if not text_path.startswith("raw/extracted/"):
        return []
    front = parse_front_matter(source_text)
    raw_warnings = front.values.get("warnings")
    if not isinstance(raw_warnings, list):
        return [
            f"{SOURCE_EXTRACTION_PREFIX} текст получен из вспомогательной "
            "extracted-копии, но её "
            "ограничения чтения не удалось определить."
        ]
    return [
        f"{SOURCE_EXTRACTION_PREFIX} " + str(warning)
        for warning in raw_warnings
        if str(warning).strip()
    ]
