"""
query_spec_tool.py — Tool-адаптер для вызова детерминированного компилятора QuerySpec (query_spec.py).
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
if str(_SKILL_DIR) in sys.path:
    sys.path.remove(str(_SKILL_DIR))
sys.path.insert(0, str(_SKILL_DIR))

from datetime import date
from typing import Optional

try:
    from utils.tools.base import Tool, ToolResult
    from utils.tools.registry import REGISTRY
except Exception:  # offline DI tests may not have PyYAML/tool package dependencies
    from typing import Any

    class ToolResult:
        def __init__(self, ok: bool, output: Any = None, summary: str = "", error: Optional[str] = None):
            self.ok, self.output, self.summary, self.error = ok, output, summary, error

    class Tool:
        def __init__(self, name, description, args_schema, returns, run, category="data"):
            self.name, self.description, self.args_schema = name, description, args_schema
            self.returns, self.run, self.category = returns, run, category

    class _OfflineRegistry:
        def register(self, *args, **kwargs):
            return None

    REGISTRY = _OfflineRegistry()


async def run_query_spec(ctx, spec: Optional[dict] = None, **kwargs) -> ToolResult:
    """Скомпилировать QuerySpec IR в выгрузку. ctx = SessionState."""
    from utils.query_spec import CompileContext, compile_query_spec

    if spec is None:
        spec = kwargs or {}

    injected_schema = kwargs.pop("_schema", None)
    injected_registry = kwargs.pop("_registry", None)
    if injected_schema is None:
        from utils.schema.loader import get_schema
        injected_schema = get_schema()

    cctx = CompileContext(
        ctx=ctx,
        emit=getattr(ctx, "emit", None),
        schema=injected_schema,
        now=date.today()
    )
    res = await compile_query_spec(cctx, spec, registry=injected_registry)
    if not res.ok:
        return ToolResult(ok=False, error=res.error)
    
    analysis_narrative = ""
    if res.analysis_df_id:
        try:
            from ior_hypothesis import generate_hypothesis_narrative
            analysis_df = ctx.get_df(res.analysis_df_id)
            session_id = (
                getattr(ctx, "session_id", None)
                or getattr(ctx, "id", None)
                or f"query_spec_{id(ctx)}"
            )
            original_intent = (
                spec.get("original_user_intent")
                or spec.get("user_intent")
                or spec.get("intent")
                or "Smart QuerySpec"
            )
            analysis_narrative = await generate_hypothesis_narrative(
                str(original_intent),
                analysis_df,
                session_id=str(session_id),
                preset_name="ior_hypothesis",
                analysis_context={
                    "original_user_intent": original_intent,
                    "spec_resolved": res.spec_resolved or spec,
                },
            )
            granularity_warning = (res.spec_resolved or {}).get("analysis_granularity_warning")
            if granularity_warning:
                analysis_narrative = f"### Ограничение гранулярности аналитики\n{granularity_warning}\n\n{analysis_narrative}"
        except Exception as analysis_error:
            analysis_narrative = f"Аналитическая часть QuerySpec не сформирована: {analysis_error}"

    out = {
        "df_id": res.df_id,
        "analysis_df_id": res.analysis_df_id,
        "file_id": res.file_id,
        "spec_resolved": res.spec_resolved,
        "lineage": res.lineage,
        "funnel": res.funnel,
        "warnings": res.warnings,
        "analysis_narrative": analysis_narrative,
    }
    summary = (f"QuerySpec -> {res.file_id or res.df_id}; готовая аналитика находится в analysis_narrative"
               + (f" ({len(res.warnings)} warnings)" if res.warnings else ""))
    return ToolResult(ok=True, output=out, summary=summary)


REGISTRY.register(Tool(
    name="run_query_spec",
    description=(
        "Скомпилировать декларативный QuerySpec (JSON-IR одной выгрузки) в файл. "
        "ИСПОЛЬЗУЙ для табличных выгрузок с join/агрегатами/деньгами/окнами — "
        "детерминированный компилятор сам строит lineage (query->pre_aggregate->"
        "join->aggregate->derived->window->sort->export). Деньги — ТОЛЬКО через join "
        "к fin_impact/recovery (суммы main заполнены ~2.26%). Период — intent "
        "(filters[kind=period]), границы считает компилятор."
    ),
    args_schema={
        "type": "object",
        "properties": {
            "spec": {"type": "object", "description": "QuerySpec v1"},
        },
        "required": ["spec"],
    },
    returns="{df_id, analysis_df_id, file_id, spec_resolved, lineage, funnel, warnings, analysis_narrative: готовый пользовательский аналитический отчёт с гипотезами}",
    run=run_query_spec,
    category="query",
))
