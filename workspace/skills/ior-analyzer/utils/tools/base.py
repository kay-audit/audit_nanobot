"""
Tool — атомарная операция, которую может вызывать LLM (Planner).

Каждый tool описывает:
 * name + description — для LLM-каталога
 * args_schema — JSON-schema аргументов (валидируется перед вызовом)
 * returns — что вернёт (df_id | file_id | scalar | ...)
 * run() — собственно реализация

Tool НЕ знает про SessionState напрямую — она передаётся как `ctx`
параметр. Это упрощает тестирование (можно вызывать без сессии).
"""
from __future__ import annotations
import sys
from pathlib import Path

_FILE_PATH = Path(__file__).resolve()
_SKILL_DIR = _FILE_PATH.parent
while _SKILL_DIR.parent != _SKILL_DIR:
    if (_SKILL_DIR / "SKILL.md").exists() or _SKILL_DIR.name == "ior-analyzer":
        break
    _SKILL_DIR = _SKILL_DIR.parent

_SCRIPTS_DIR = _SKILL_DIR / "scripts"
_UTILS_DIR = _SKILL_DIR / "utils"

for _dir in (_SKILL_DIR, _SCRIPTS_DIR, _UTILS_DIR):
    _sdir = str(_dir)
    if _sdir not in sys.path:
        sys.path.insert(0, _sdir)


import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class ToolResult:
    """Результат tool-вызова."""
    ok: bool
    output: Any = None            # df_id, file_id, scalar, dict, ...
    summary: str = ""             # короткое описание для LLM history
    error: Optional[str] = None
    duration_ms: int = 0


@dataclass
class Tool:
    """Описание + реализация одного tool'а."""
    name: str
    description: str
    args_schema: dict             # JSON-schema (для LLM и валидации)
    returns: str                  # "df_id" | "file_id" | "scalar" | ...
    run: Callable[..., Awaitable[ToolResult]]
    category: str = "data"        # "data" | "transform" | "export" | "analysis"

    def to_llm_descriptor(self) -> dict:
        """Компактное описание для prompt'а planner'а."""
        return {
            "name": self.name,
            "description": self.description,
            "args": self.args_schema,
            "returns": self.returns,
            "category": self.category,
        }
