"""Типы данных безопасного workflow."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class LLMRequest:
    """Один запрос к модели без файловых инструментов."""

    system_prompt: str
    user_prompt: str
    operation: str


@dataclass(frozen=True)
class LLMResponse:
    """Нормализованный текстовый ответ провайдера."""

    content: str


@dataclass
class FileChange:
    """Полное состояние файла до и после изменения."""

    action: Literal["create", "update"]
    path: str
    reason: str
    before_content: str | None
    after_content: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "FileChange":
        action = value.get("action")
        if action not in {"create", "update"}:
            raise ValueError(f"недопустимое action: {action!r}")
        before = value.get("before_content")
        if before is not None and not isinstance(before, str):
            raise TypeError("before_content должен быть строкой или null")
        return cls(
            action=action,
            path=_required_string(value, "path"),
            reason=_required_string(value, "reason"),
            before_content=before,
            after_content=_required_string(value, "after_content"),
        )


@dataclass
class ChangeSet:
    """Машиночитаемая транзакция внутри Markdown-Proposal."""

    version: int
    source_path: str
    source_sha256: str
    summary: str
    reading_limitations: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    links_added: list[str] = field(default_factory=list)
    links_removed: list[str] = field(default_factory=list)
    verification: list[str] = field(default_factory=list)
    changes: list[FileChange] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "source_path": self.source_path,
            "source_sha256": self.source_sha256,
            "summary": self.summary,
            "reading_limitations": self.reading_limitations,
            "conflicts": self.conflicts,
            "links_added": self.links_added,
            "links_removed": self.links_removed,
            "verification": self.verification,
            "changes": [change.to_dict() for change in self.changes],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ChangeSet":
        version = value.get("version")
        if type(version) is not int:
            raise TypeError("version должен быть целым числом")
        changes_value = value.get("changes")
        if not isinstance(changes_value, list):
            raise TypeError("changes должен быть массивом")
        if not all(isinstance(item, dict) for item in changes_value):
            raise TypeError("каждый changes[] должен быть объектом")
        return cls(
            version=version,
            source_path=_required_string(value, "source_path"),
            source_sha256=_required_string(value, "source_sha256"),
            summary=_required_string(value, "summary"),
            reading_limitations=_strict_string_list(
                value, "reading_limitations"
            ),
            conflicts=_strict_string_list(value, "conflicts"),
            links_added=_strict_string_list(value, "links_added"),
            links_removed=_strict_string_list(value, "links_removed"),
            verification=_strict_string_list(value, "verification"),
            changes=[
                FileChange.from_dict(item)
                for item in changes_value
            ],
        )


@dataclass(frozen=True)
class LintIssue:
    """Одна детерминированная lint-проблема."""

    level: Literal["error", "warning", "info"]
    path: str
    title: str
    description: str
    evidence: str
    impact: str
    action: str
    manual_review: str

    def fingerprint(self) -> tuple[str, str, str, str, str]:
        return (
            self.level,
            self.path,
            self.title,
            self.description,
            self.evidence,
        )


@dataclass(frozen=True)
class SelectedContext:
    """Контекст, фактически выбранный локальным контроллером."""

    paths: tuple[str, ...]
    text: str
    truncated_paths: tuple[str, ...] = ()
    omitted_paths: tuple[str, ...] = ()


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _required_string(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str):
        raise TypeError(f"{key} должен быть строкой")
    return item


def _strict_string_list(value: dict[str, Any], key: str) -> list[str]:
    items = value.get(key)
    if not isinstance(items, list) or not all(
        isinstance(item, str) for item in items
    ):
        raise TypeError(f"{key} должен быть массивом строк")
    return list(items)
