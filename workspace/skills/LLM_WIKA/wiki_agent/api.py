"""Публичный Python API LLM-Wiki.

CLI использует этот же класс, поэтому запуск из Python и терминала имеет
одинаковые проверки безопасности и одинаковое поведение.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .models import LLMRequest
from .jira_confluence import (
    JiraIngestResult,
    PrepareResult,
    card_documents,
    ingest_jira_json,
    prepare_jira_confluence,
)
from .proposal import (
    PROPOSAL_SIZE_MULTIPLIER,
    apply_proposal,
    load_changeset_from_proposal,
    proposal_revision,
    render_change_preview,
)
from .provider import LLMProvider, StubProvider, provider_from_settings
from .semantic import FaissPageIndex, IndexStatus, SemanticHit
from .skills import (
    IngestResult,
    LintRunResult,
    QueryRunResult,
    run_ingest,
    run_lint,
    run_query,
)
from .watcher import SourceInboxWatcher, run_watch
from .wiki import load_catalog
from .workspace import Workspace


@dataclass(frozen=True)
class ProposalPreview:
    path: str
    revision: str
    changes: tuple[tuple[str, str], ...]
    diff: str


@dataclass(frozen=True)
class ApplyResult:
    proposal_path: str
    changed_files: tuple[str, ...]
    index_status: IndexStatus | None
    index_error: str | None = None


@dataclass(frozen=True)
class DoctorResult:
    root: Path
    python_version: str
    agent_python_supported: bool
    provider: str
    sdk_installed: bool
    credentials_configured: bool
    external_context_allowed: bool
    tls_verification_enabled: bool
    ca_bundle_file: Path | None
    faiss_installed: bool
    embeddings_installed: bool
    query_search: str
    index_status: IndexStatus
    ping_response: str | None = None


class JiraOperations:
    """Jira/Confluence-режим того же WikiAgent."""

    def __init__(self, agent: "WikiAgent") -> None:
        self._agent = agent

    def prepare(self, key: str | None = None) -> PrepareResult:
        return self._agent.prepare_jira_confluence(key=key)

    def load(self, key: str) -> PrepareResult:
        """Добавить выбранную Jira или проект и связанные Confluence в поиск."""
        return self._agent.prepare_jira_confluence(key=key)

    def ingest(
        self,
        source_path: str,
        *,
        request: str = "",
    ) -> JiraIngestResult:
        return self._agent.ingest_jira(source_path, request=request)

    def query(
        self,
        question: str,
        *,
        dry_run: bool = False,
        save_markdown: bool = False,
    ) -> QueryRunResult:
        provider = StubProvider() if dry_run else self._agent._llm_provider()
        return run_query(
            self._agent.workspace,
            self._agent.settings,
            provider,
            question,
            dry_run=dry_run,
            save_markdown=save_markdown,
            report_mode="jira",
        )


class WikiAgent:
    """Высокоуровневый интерфейс для запуска LLM-Wiki из Python-кода."""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        settings: Settings | None = None,
        provider: LLMProvider | None = None,
    ) -> None:
        if settings is None:
            start = Path(root).resolve() if root is not None else Path.cwd()
            discovered = Workspace.discover(start)
            settings = Settings.from_root(discovered.root)
        elif root is not None and Path(root).resolve() != settings.root:
            raise ValueError("root и settings.root должны совпадать")
        self.settings = settings
        self.workspace = Workspace(
            settings.root,
            max_file_chars=settings.max_file_chars,
        )
        self._provider = provider
        self.jira = JiraOperations(self)

    def doctor(self, *, ping: bool = False) -> DoctorResult:
        provider_dependency = (
            "requests"
            if self.settings.provider in {"gigachat_internal", "minimax"}
            else "langchain_gigachat"
        )
        sdk_installed = importlib.util.find_spec(provider_dependency) is not None
        faiss_installed = importlib.util.find_spec("faiss") is not None
        embeddings_installed = (
            importlib.util.find_spec("sentence_transformers") is not None
        )
        index_status = self.index_status()
        ping_response = None
        if ping:
            response = self._llm_provider().complete(
                LLMRequest(
                    system_prompt=(
                        "Ответь только словом OK. Не используй внешние знания."
                    ),
                    user_prompt="Проверка соединения.",
                    operation="doctor",
                )
            )
            ping_response = response.content
        return DoctorResult(
            root=self.workspace.root,
            python_version=sys.version.split()[0],
            agent_python_supported=(
                self.settings.python_supported_by_agent_stack
            ),
            provider=self.settings.provider,
            sdk_installed=sdk_installed,
            credentials_configured=self.settings.credentials_configured,
            external_context_allowed=self.settings.allow_external_context,
            tls_verification_enabled=self.settings.verify_ssl_certs,
            ca_bundle_file=self.settings.ca_bundle_file,
            faiss_installed=faiss_installed,
            embeddings_installed=embeddings_installed,
            query_search=self.settings.query_search,
            index_status=index_status,
            ping_response=ping_response,
        )

    def query(
        self,
        question: str,
        *,
        dry_run: bool = False,
        save_markdown: bool = False,
    ) -> QueryRunResult:
        provider = StubProvider() if dry_run else self._llm_provider()
        return run_query(
            self.workspace,
            self.settings,
            provider,
            question,
            dry_run=dry_run,
            save_markdown=save_markdown,
            report_mode="wiki",
        )

    def lint(self, *, technical_only: bool = False) -> LintRunResult:
        provider = (
            StubProvider() if technical_only else self._llm_provider()
        )
        return run_lint(
            self.workspace,
            self.settings,
            provider,
            technical_only=technical_only,
        )

    def ingest(
        self,
        source_path: str,
        *,
        request: str = "",
    ) -> IngestResult:
        return run_ingest(
            self.workspace,
            self.settings,
            self._llm_provider(),
            source_path,
            user_request=request,
        )

    def prepare_jira_confluence(self, *, key: str | None = None) -> PrepareResult:
        """Подготовить документы, summaries и двусторонние карточки."""

        result = prepare_jira_confluence(
            self.workspace,
            self._llm_provider(),
            llm_model_name=self.settings.model,
            key=key,
        )
        self.build_index()
        return result

    def ingest_jira(
        self,
        source_path: str,
        *,
        request: str = "",
    ) -> JiraIngestResult:
        """Создать один Proposal из связанного Jira/Confluence JSON."""

        return ingest_jira_json(
            self.workspace,
            self._llm_provider(),
            source_path,
            max_file_chars=self.settings.max_file_chars,
            max_context_chars=self.settings.max_context_chars,
            max_query_pages=self.settings.max_query_pages,
            user_request=request,
            llm_model_name=self.settings.model,
        )

    def inspect_proposal(self, proposal_path: str) -> ProposalPreview:
        path = self.workspace.resolve(
            proposal_path,
            must_exist=True,
            allowed_roots=("proposals",),
        )
        relative = self.workspace.relative(path)
        content = self.workspace.read_text(
            relative,
            max_chars=(
                self.workspace.max_file_chars * PROPOSAL_SIZE_MULTIPLIER
            ),
        )
        changeset = load_changeset_from_proposal(content)
        return ProposalPreview(
            path=relative,
            revision=proposal_revision(content),
            changes=tuple(
                (change.action, change.path)
                for change in changeset.changes
            ),
            diff=render_change_preview(changeset),
        )

    def apply(
        self,
        proposal_path: str,
        *,
        expected_revision: str | None = None,
        rebuild_index: bool = True,
    ) -> ApplyResult:
        preview = self.inspect_proposal(proposal_path)
        changed = apply_proposal(
            self.workspace,
            preview.path,
            confirmed_path=preview.path,
            expected_revision=expected_revision or preview.revision,
        )
        index_status: IndexStatus | None = None
        index_error: str | None = None
        if rebuild_index:
            try:
                index_status = self.build_index()
            except Exception as exc:
                # Wiki уже безопасно применена; ошибка производного индекса
                # не должна откатывать подтверждённый Proposal.
                index_error = f"{type(exc).__name__}: {exc}"
        return ApplyResult(
            proposal_path=preview.path,
            changed_files=tuple(changed),
            index_status=index_status,
            index_error=index_error,
        )

    def build_index(self) -> IndexStatus:
        return self._semantic_index(
            allow_download=self.settings.allow_embedding_download
        ).build(self._search_documents())

    def index_status(self) -> IndexStatus:
        return self._semantic_index().status(self._search_documents())

    def search_index(self, question: str) -> tuple[SemanticHit, ...]:
        hits = self._semantic_index().search(
            self._search_documents(),
            question.strip(),
            limit=min(
                self.settings.faiss_top_k,
                self.settings.max_query_pages,
            ),
            min_score=self.settings.faiss_min_score,
        )
        return tuple(hits)

    def source_watcher(
        self,
        *,
        settle_seconds: float = 2.0,
        include_existing: bool = False,
    ) -> SourceInboxWatcher:
        """Вернуть watcher для управляемого пользователем event loop."""

        return SourceInboxWatcher(
            self.workspace,
            lambda path: self.ingest(path),
            settle_seconds=settle_seconds,
            include_existing=include_existing,
        )

    def watch(
        self,
        *,
        interval_seconds: float = 1.0,
        settle_seconds: float = 2.0,
        include_existing: bool = False,
    ) -> int:
        """Непрерывно создавать Proposal для новых стабильных источников."""

        return run_watch(
            self.workspace,
            self.settings,
            self._llm_provider(),
            interval_seconds=interval_seconds,
            settle_seconds=settle_seconds,
            include_existing=include_existing,
        )

    def _llm_provider(self) -> LLMProvider:
        return self._provider or provider_from_settings(self.settings)

    def _semantic_index(
        self,
        *,
        allow_download: bool = False,
    ) -> FaissPageIndex:
        return FaissPageIndex(
            workspace_root=self.settings.root,
            cache_dir=self.settings.faiss_cache_dir,
            model_cache_dir=self.settings.embedding_cache_dir,
            vector_cache_dir=self.settings.embedding_vector_cache_dir,
            model_name=self.settings.embedding_model,
            allow_model_download=allow_download,
            embedding_device=self.settings.embedding_device,
        )

    def _pages(self):
        return [
            item
            for item in load_catalog(self.workspace).documents
            if item.path.startswith("wiki/pages/")
        ]

    def _search_documents(self):
        return [*self._pages(), *card_documents(self.workspace)]


def open_agent(root: str | Path | None = None) -> WikiAgent:
    """Открыть LLM-Wiki с настройками из окружения."""

    return WikiAgent(root)
