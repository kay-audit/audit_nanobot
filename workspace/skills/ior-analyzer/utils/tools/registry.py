"""
Реестр всех доступных tools. Импорт `backend.agent.tools` авто-регистрирует
их через side-effect (импорт каждого модуля - register()).

Использование:
    from backend.agent.tools import REGISTRY
    catalog = REGISTRY.llm_catalog()
    result = await REGISTRY.execute("query", {"table": "...", "where": {...}}, ctx)
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
import time
from typing import Any, Optional

from utils.tools.base import Tool, ToolResult

logger = logging.getLogger(__name__)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            logger.warning("[Registry] tool %s переопределён", tool.name)
        self._tools[tool.name] = tool
        logger.debug("[Registry] зарегистрирован tool: %s (%s)",
                     tool.name, tool.category)

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def by_category(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for t in self._tools.values():
            out.setdefault(t.category, []).append(t.name)
        return out

    def llm_catalog(self) -> list[dict]:
        """Каталог для system prompt'а планировщика."""
        return [t.to_llm_descriptor() for t in self._tools.values()]

    def llm_catalog_compact(self) -> str:
        """Текстовое представление каталога (компактнее JSON'а)."""
        lines = []
        for cat, names in self.by_category().items():
            lines.append(f"\n[{cat}]")
            for n in names:
                t = self._tools[n]
                args = ", ".join(f"{k}: {v.get('type', 'any')}"
                                 for k, v in t.args_schema.get("properties", {}).items())
                lines.append(f" * {t.name}({args}) -> {t.returns} - {t.description}")
        return "\n".join(lines)

    async def execute(self, name: str, args: dict, ctx: Any) -> ToolResult:
        """Запуск tool'а с args + context. Catches exceptions -> ToolResult."""
        tool = self.get(name)
        if tool is None:
            return ToolResult(
                ok=False,
                error=f"tool '{name}' не зарегистрирован.\n"
                      f"Доступны: {self.names()}"
            )
        
        # Фильтруем args по сигнатуре функции - LLM любит подмешивать
        # plan-метаданные (produces, depends_on, id, step_id) в args.
        # Не дропаем если функция принимает **kwargs.
        filtered_args, dropped = _filter_kwargs(tool.run, args)
        if dropped:
            logger.debug("[Registry] tool %s: дропнул unknown kwargs %s",
                         name, list(dropped))
        
        started = time.perf_counter()
        try:
            # Tool.run может быть async или sync
            if inspect.iscoroutinefunction(tool.run):
                result = await tool.run(ctx=ctx, **filtered_args)
            else:
                result = tool.run(ctx=ctx, **filtered_args)
            
            if not isinstance(result, ToolResult):
                # Tool вернул сырой результат - обернём
                result = ToolResult(ok=True, output=result, summary=str(result)[:200])
            
            result.duration_ms = int((time.perf_counter() - started) * 1000)
            return result
        except Exception as e:  # noqa: BLE001
            logger.exception("[Registry] tool %s упал: %s", name, e)
            return ToolResult(
                ok=False,
                error=f"{type(e).__name__}: {e}",
                duration_ms=int((time.perf_counter() - started) * 1000)
            )


def _filter_kwargs(fn, args: dict) -> tuple[dict, set]:
    """Возвращает {kept, dropped}. Если у функции есть **kwargs — ничего не дропает.
    Иначе оставляет только параметры, объявленные в сигнатуре (без ctx).
    """
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return args, set()
    
    has_var_kw = any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    )
    if has_var_kw:
        return args, set()
    
    allowed = {
        name for name, p in sig.parameters.items()
        if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                      inspect.Parameter.KEYWORD_ONLY)
        and name != "ctx"
    }
    kept = {k: v for k, v in args.items() if k in allowed}
    dropped = set(args.keys()) - set(kept.keys())
    return kept, dropped


# Глобальный singleton — заполняется через side-effect импорта tools/__init__.py
REGISTRY = ToolRegistry()
