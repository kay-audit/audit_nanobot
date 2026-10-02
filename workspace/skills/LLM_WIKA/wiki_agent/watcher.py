"""Наблюдение за новыми первоисточниками без постоянного реестра."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .errors import SecurityError, ValidationError, WikiAgentError
from .extraction import SUPPORTED
from .provider import LLMProvider
from .skills.ingest import IngestResult, run_ingest
from .workspace import Workspace
from .config import Settings


@dataclass(frozen=True)
class FileSignature:
    size: int
    modified_ns: int


@dataclass
class PendingSource:
    signature: FileSignature
    stable_since: float
    attempted: bool = False


@dataclass(frozen=True)
class WatchEvent:
    source_path: str
    result: IngestResult | None = None
    error: str | None = None


class SourceInboxWatcher:
    """Обработать только файлы, появившиеся после запуска watcher."""

    def __init__(
        self,
        workspace: Workspace,
        processor: Callable[[str], IngestResult],
        *,
        settle_seconds: float,
        include_existing: bool = False,
    ) -> None:
        if settle_seconds < 0:
            raise ValidationError(
                "settle_seconds не может быть отрицательным"
            )
        self.workspace = workspace
        self.processor = processor
        self.settle_seconds = settle_seconds
        current = self._source_signatures()
        self.ignored = set() if include_existing else set(current)
        self.pending: dict[str, PendingSource] = {}
        self.completed: set[str] = set()

    @property
    def initial_count(self) -> int:
        return len(self.ignored)

    def poll(self, *, now: float | None = None) -> list[WatchEvent]:
        moment = time.monotonic() if now is None else now
        current = self._source_signatures()
        events: list[WatchEvent] = []

        for removed in set(self.pending) - set(current):
            self.pending.pop(removed, None)
        for path in sorted(current):
            if path in self.ignored or path in self.completed:
                continue
            signature = current[path]
            state = self.pending.get(path)
            if state is None or state.signature != signature:
                self.pending[path] = PendingSource(signature, moment)
                continue
            if state.attempted:
                continue
            if moment - state.stable_since < self.settle_seconds:
                continue

            state.attempted = True
            try:
                result = self.processor(path)
            except WikiAgentError as exc:
                events.append(WatchEvent(path, error=str(exc)))
            except Exception as exc:
                events.append(
                    WatchEvent(
                        path,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
            else:
                self.completed.add(path)
                self.pending.pop(path, None)
                events.append(WatchEvent(path, result=result))
        return events

    def _source_signatures(self) -> dict[str, FileSignature]:
        source_root = self.workspace.resolve(
            "raw/sources",
            must_exist=True,
            allowed_roots=("raw/sources",),
        )
        result: dict[str, FileSignature] = {}
        for path in sorted(source_root.iterdir()):
            if path.name.startswith(".") or path.suffix.lower() not in SUPPORTED:
                continue
            if path.is_symlink():
                raise SecurityError(
                    f"Symlink в raw/sources запрещён: {path.name}"
                )
            if not path.is_file():
                continue
            relative = self.workspace.relative(path)
            # Повторно применяем строгую проверку прямого source-пути.
            validated = self.workspace.source_path(relative)
            stat = validated.stat()
            result[relative] = FileSignature(
                size=stat.st_size,
                modified_ns=stat.st_mtime_ns,
            )
        return result


def run_watch(
    workspace: Workspace,
    settings: Settings,
    provider: LLMProvider,
    *,
    interval_seconds: float,
    settle_seconds: float,
    include_existing: bool = False,
) -> int:
    """Непрерывно создавать Proposal для новых стабильных источников."""

    if interval_seconds <= 0:
        raise ValidationError(
            "interval_seconds должен быть больше нуля"
        )

    watcher = SourceInboxWatcher(
        workspace,
        lambda path: run_ingest(
            workspace,
            settings,
            provider,
            path,
        ),
        settle_seconds=settle_seconds,
        include_existing=include_existing,
    )
    print("Watcher запущен. Наблюдение: raw/sources/")
    if include_existing:
        print("Будут обработаны поддерживаемые существующие файлы.")
    else:
        print(
            "Существующие файлы пропущены: "
            f"{watcher.initial_count}. Обрабатываются только новые."
        )
    print("Для остановки нажмите Ctrl+C.")

    while True:
        for event in watcher.poll():
            if event.error is not None:
                print(
                    f"Ошибка ingest {event.source_path}: {event.error}",
                    flush=True,
                )
                print(
                    "Файл не будет повторно обработан, пока не изменится.",
                    flush=True,
                )
                continue
            assert event.result is not None
            if event.result.extracted_path:
                print(
                    "Текстовая копия создана автоматически: "
                    f"{event.result.extracted_path}",
                    flush=True,
                )
            print(
                f"Proposal создан автоматически: "
                f"{event.result.proposal_path}",
                flush=True,
            )
            print(
                "Wiki не изменена. Проверьте Proposal и выполните apply.",
                flush=True,
            )
            print(
                "Команда подтверждения: python3.12 -m wiki_agent apply "
                f'"{event.result.proposal_path}"',
                flush=True,
            )
        time.sleep(interval_seconds)
