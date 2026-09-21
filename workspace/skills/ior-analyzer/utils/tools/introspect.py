"""
Интроспект-инструменты — дают LLM ВИДЕТЬ реальные данные перед действием
(основа парадигмы «наблюдай + действуй» вместо угадывания вслепую).

 * search_values    — где живёт значение? топ реальных (колонка, значение, count)
 * probe            — даст ли фильтр строки? (count без выгрузки) — против дампа
 * distinct_values  — какие реальные значения в колонке? (живой SELECT DISTINCT)
 * describe_schema  — структура таблицы: колонки, типы, filled_pct, ключи

Все — read-only, дешёвые, не создают df/файлов. Возвращают компактный dict для
LLM-контекста. Регистрируются в REGISTRY как обычные tools.
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


import asyncio
import logging
from typing import Optional

from utils.resolve.grounding import search_values as _search_values
from utils.schema.loader import get_schema
from utils.tools.base import Tool, ToolResult
from utils.tools.registry import REGISTRY
from utils.data_store import get_data_store

logger = logging.getLogger(__name__)

_PROBE_LIMIT = 1000

# ——— search_values: где реально живёт значение ————————————————————————


async def search_values(ctx, query: str, columns: Optional[list] = None, top_k: int = 8) -> ToolResult:
    """Найти, в какой РЕАЛЬНОЙ колонке витрины лежит значение (устойчиво к опечаткам).

    Используй ПЕРЕД фильтрацией, если не уверен в колонке: 'Волго-Вятский банк'
    лежит в org_struct_lvl_3_name, а не в lvl_2. Аббревиатуры (СЗБ) разворачивай
    сам перед поиском ('Северо-Западный').
    """
    cands = _search_values(query, top_k=top_k, columns=columns)
    if not cands:
        return ToolResult(
            ok=True,
            output={"query": query, "candidates": []},
            summary=f"'{query}': похожих значений в витрине не найдено "
                    + "(возможно опечатка/аббревиатура — разверни и попробуй иначе)"
        )
    out = [c.to_llm() for c in cands]
    top = cands[0]
    return ToolResult(
        ok=True,
        output={"query": query, "candidates": out},
        summary=f"'{query}' -> {top.column} (напр. '{top.value}', {top.count:,} строк)"
                + (f" и ещё {len(cands)-1}" if len(cands) > 1 else "")
    )


# ——— probe: даст ли фильтр строки (без выгрузки) ————————————————————————


async def probe(ctx, table: str, where: Optional[dict] = None) -> ToolResult:
    """Проверить, СКОЛЬКО строк даст фильтр, НЕ выгружая их. Защита от пустых/
    мусорных выгрузок: если 0 — фильтр почти точно неверен (не та колонка/значение).
    """
    store = get_data_store()
    t = get_schema().get(table)
    probe_col = None
    if t and t.columns:
        probe_col = next((c.name for c in t.columns if c.name in ("incdnt_id", "incdnt_sid")), t.columns[0].name)
    
    try:
        df = await asyncio.to_thread(
            store.query,
            table=table,
            where=where,
            columns=[probe_col] if probe_col else None,
            limit=_PROBE_LIMIT
        )
    except Exception as e:  # noqa: BLE001
        return ToolResult(ok=False, error=f"probe упал: {type(e).__name__}: {e}")
    
    n = len(df)
    capped = n >= _PROBE_LIMIT
    matched = n > 0

    if not matched and where:
        from utils.resolve.grounding import diagnose_empty
        diag = diagnose_empty(table, where)
        return ToolResult(
            ok=True,
            output={
                "matched": False,
                "rows": 0,
                "where": where,
                "diagnosis": diag.message,
                "corrections": diag.corrections
            },
            summary=f"probe: 0 строк по фильтру — {('неверная колонка?' if diag.likely_wrong else diag.message)}"
        )
    
    return ToolResult(
        ok=True,
        output={"matched": matched, "rows": n, "capped": capped, "where": where},
        summary=f"probe: {'≥' if capped else ''}{n:,} строк по фильтру"
    )


# ——— distinct_values: реальные значения колонки ————————————————————————


async def distinct_values(ctx, table: str, column: str, contains: Optional[str] = None, limit: int = 50) -> ToolResult:
    """Живой SELECT DISTINCT по колонке — реальные значения справочника.
    `contains` — отфильтровать значения по подстроке (регистронезависимо).
    Для high-card колонок (процессы ~89б) обязательно задавай contains.
    """
    try:
        limit_val = int(limit)
        if limit_val <= 0:
            limit_val = 50
        limit = limit_val
    except (TypeError, ValueError):
        limit = 50

    store = get_data_store()
    try:
        fetch_n = max(int(limit) * 4, 200) if contains else int(limit)
        vals = await asyncio.to_thread(
            store.fetch_distinct_values,
            table=table,
            column=column,
            max_values=fetch_n
        )
    except Exception as e:  # noqa: BLE001
        return ToolResult(ok=False, error=f"distinct_values упал: {type(e).__name__}: {e}")
    
    if contains:
        sub = contains.lower()
        vals = [v for v in vals if sub in str(v).lower()][:int(limit)]
    else:
        vals = vals[:int(limit)]
        
    return ToolResult(
        ok=True,
        output={"table": table, "column": column, "values": vals, "count": len(vals)},
        summary=f"{column}: {len(vals)} значений"
                + (f" с '{contains}'" if contains else "")
                + (f" (напр. {vals[0]!r})" if vals else " (пусто)")
    )


# ——— describe_schema: структура таблицы ————————————————————————


async def describe_schema(ctx, table: Optional[str] = None) -> ToolResult:
    """Структура БД: колонки, типы, filled_pct, ключи джойнов. Без table — вся схема.
    Обрати внимание на filled_pct: суммовые колонки main заполнены ~2.26% —
    для денежных метрик используй join к fin_impact/recovery (заполнены ~99%).
    """
    schema = get_schema()
    if table:
        t = schema.get(table)
        if t is None:
            return ToolResult(ok=False, error=f"Таблицы {table!r} нет. Доступны: {schema.table_names()}")
        text = t.to_llm_snippet(verbose=True)
        return ToolResult(
            ok=True,
            output={"table": table, "schema": text},
            summary=f"схема {table}: {len(t.columns)} колонок"
        )
    text = schema.to_llm_snippet()
    return ToolResult(
        ok=True,
        output={"schema": text},
        summary=f"схема БД: {len(schema.table_names())} таблиц"
    )


# ——— Регистрация ————————————————————————


REGISTRY.register(Tool(
    name="search_values",
    description=("Найти, в какой РЕАЛЬНОЙ колонке витрины лежит значение "
                 "(устойчиво к опечаткам). Вызывай ПЕРЕД фильтром, если не уверен "
                 "в колонке. Аббревиатуры разворачивай сам (СЗБ->Северо-Западный)."),
    args_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "значение/фраза для поиска"},
            "columns": {"type": "array", "description": "ограничить колонками (опц.)"},
            "top_k": {"type": "integer", "default": 8},
        },
        "required": ["query"],
    },
    returns="{candidates: [{column, value, count, filled_pct, score}]}",
    run=search_values,
    category="introspect",
))

REGISTRY.register(Tool(
    name="probe",
    description=("Проверить, СКОЛЬКО строк даст фильтр, НЕ выгружая. Защита от "
                 "пустых выгрузок: 0 строк -> фильтр неверен (диагностика по реальным "
                 "данным укажет правильную колонку)."),
    args_schema={
        "type": "object",
        "properties": {
            "table": {"type": "string"},
            "where": {"type": "object", "description": "фильтры (как в query)"},
        },
        "required": ["table"],
    },
    returns="{matched, rows, diagnosis?, corrections?}",
    run=probe,
    category="introspect",
))

REGISTRY.register(Tool(
    name="distinct_values",
    description=("Живой SELECT DISTINCT по колонке — реальные значения справочника. "
                 "Для high-card колонок (процессы) задавай contains."),
    args_schema={
        "type": "object",
        "properties": {
            "table": {"type": "string"},
            "column": {"type": "string"},
            "contains": {"type": "string", "description": "фильтр по подстроке (опц.)"},
            "limit": {"type": "integer", "default": 50},
        },
        "required": ["table", "column"],
    },
    returns="{values: [...], count}",
    run=distinct_values,
    category="introspect",
))

REGISTRY.register(Tool(
    name="describe_schema",
    description=("Структура БД: колонки, типы, filled_pct, ключи. Без table — вся "
                 "схема. filled_pct подсказывает: суммы main заполнены ~2.26% -> деньги через "
                 "join к fin_impact/recovery."),
    args_schema={
        "type": "object",
        "properties": {
            "table": {"type": "string", "description": "имя таблицы (опц.)"},
        },
        "required": [],
    },
    returns="{schema: str}",
    run=describe_schema,
    category="introspect",
))
