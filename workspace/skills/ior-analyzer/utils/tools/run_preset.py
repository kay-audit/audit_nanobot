"""
Tool: run_preset(skill_id, params) — запустить один из 6 готовых
ipynb-скриптов БД через Papermill (legacy notebook runner).

Это back-compat-обёртка: планировщик может в одном шаге плана
обратиться к проверенному скрипту, когда задача попадает под один из
готовых отчётов («Выгрузи ИОР за период», «Полное досье EVE-...» и т.д.).

Для нетривиальных запросов planner соберёт цепочку из atomic-tools
(query / filter / top_n / join / export).
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
from typing import Any

import pandas as pd

from utils.tools.base import Tool, ToolResult
from utils.tools.registry import REGISTRY
from utils.data_store import get_data_store
try:
    from backend.skills.registry import get_registry as get_skill_registry
    from backend.skills.runners.notebook_runner import get_runner
except Exception:
    def get_skill_registry(): return None
    def get_runner(): return None

logger = logging.getLogger(__name__)

_SKILL_REGISTRY = None


def _skill_ids() -> list[str]:
    global _SKILL_REGISTRY
    try:
        _SKILL_REGISTRY = _SKILL_REGISTRY or get_skill_registry()
        return [s.skill_id for s in _SKILL_REGISTRY.list_all()]
    except Exception:
        return []


SKILL_ALLOWED_PARAMS = {
    "ior_hypothesis_v2": {
        "incdnt_entry_dt_begin", "incdnt_entry_dt_end",
        "status_filter", "tb_filter", "block_filter", "additional_sql_filter"
    },
    "deleted_ior_v2": {
        "incdnt_entry_dt_begin", "incdnt_entry_dt_end", "ORG_PREFIXES",
        "status_filter", "tb_filter", "block_filter", "additional_sql_filter"
    },
    "financial_consequences_ior_v2": {
        "incdnt_entry_dt_begin", "incdnt_entry_dt_end",
        "status_filter", "tb_filter", "block_filter", "additional_sql_filter"
    },
    "vozmeshenie_ior_v2": {
        "incdnt_entry_dt_begin", "incdnt_entry_dt_end",
        "status_filter", "tb_filter", "block_filter", "additional_sql_filter"
    },
    "ior_nonfinancial_consequences_v2": {
        "incdnt_entry_dt_begin", "incdnt_entry_dt_end",
        "status_filter", "tb_filter", "block_filter", "additional_sql_filter"
    },
    "credit_no_way_collect_debt_v2": {
        "incdnt_entry_dt_begin", "incdnt_entry_dt_end",
        "status_filter", "tb_filter", "block_filter", "additional_sql_filter"
    },
    "report_period_specific_ior_v2": {
        "incdnt_sid"
    },
    "ior_period_pao_sberbank_v2": {
        "incdnt_entry_dt_begin", "incdnt_entry_dt_end", "ORG_PREFIXES",
        "status_filter", "tb_filter", "block_filter", "additional_sql_filter"
    }
}


async def run_preset(ctx, skill_id: str, params: dict | None = None, emit=None) -> ToolResult:
    """Запускает Papermill-скрипт. Регистрирует получившийся xlsx как файл
    и data как dataframe (для возможной пост-обработки).
    """
    params = params or {}
    
    # 1. Авто-декодирование period/date параметров для устойчивости
    period_raw = None
    for k in ["period", "period_intent", "date_range"]:
        if k in params:
            period_raw = params.pop(k)
            
    if period_raw:
        period_text = ""
        if isinstance(period_raw, dict) and "text" in period_raw:
            period_text = period_raw["text"]
        elif isinstance(period_raw, str):
            period_text = period_raw
            
        if period_text:
            from utils.resolve.period_parser import parse_period
            from datetime import datetime, timedelta
            p_obj = parse_period(period_text)
            if p_obj:
                params["incdnt_entry_dt_begin"] = p_obj.start
                try:
                    end_dt = datetime.strptime(p_obj.end, "%Y-%m-%d") - timedelta(days=1)
                    params["incdnt_entry_dt_end"] = end_dt.strftime("%Y-%m-%d")
                except Exception:
                    params["incdnt_entry_dt_end"] = p_obj.end

    # Если даты так и не определены, берем из детерминированно распарсенного периода текущей сессии
    current_period = getattr(ctx, "current_period", None)
    if current_period:
        if "incdnt_entry_dt_begin" not in params:
            params["incdnt_entry_dt_begin"] = current_period.start
        if "incdnt_entry_dt_end" not in params:
            from datetime import datetime, timedelta
            try:
                end_dt = datetime.strptime(current_period.end, "%Y-%m-%d") - timedelta(days=1)
                params["incdnt_entry_dt_end"] = end_dt.strftime("%Y-%m-%d")
            except Exception:
                params["incdnt_entry_dt_end"] = current_period.end

    # 2. Выделение и нормализация фильтров оргструктуры, блоков, статусов и риск-профилей
    tb_val = None
    for k in ["org_struct_lvl_3_name", "tb_filter", "tb", "tb_name"]:
        if k in params:
            tb_val = params.pop(k)
            
    block_val = None
    for k in ["funct_block_lvl_2_name", "funct_block_lvl_3_name", "block_filter", "block", "block_name"]:
        if k in params:
            block_val = params.pop(k)
            
    status_val = None
    for k in ["incdnt_status_name", "status_filter", "status"]:
        if k in params:
            status_val = params.pop(k)

    source_val = None
    for k in ["src_type_lvl_2_name", "source_filter", "source"]:
        if k in params:
            source_val = params.pop(k)

    risk_profile_val = None
    risk_profile_col = None
    for k in ["risk_profile_name", "risk_profile_id", "risk_profile", "profile"]:
        if k in params:
            risk_profile_val = params.pop(k)
            break

    process_val = None
    for k in ["process_lvl_4_name", "process_filter", "process", "proc"]:
        if k in params:
            process_val = params.pop(k)

    # Канонизация и заземление категориальных значений через поисковый движок
    from utils.resolve.grounding import search_values
    if tb_val:
        hits = search_values(str(tb_val), columns=["org_struct_lvl_3_name"], min_score=0.6)
        if hits:
            tb_val = hits[0].value
        else:
            hits_any = search_values(str(tb_val), min_score=0.6)
            if hits_any:
                tb_val = hits_any[0].value

    if block_val:
        hits = search_values(str(block_val), columns=["funct_block_lvl_2_name", "funct_block_lvl_3_name", "funct_block_lvl_4_name"], min_score=0.6)
        if hits:
            block_val = hits[0].value
        else:
            hits_any = search_values(str(block_val), min_score=0.6)
            if hits_any:
                block_val = hits_any[0].value

    if status_val:
        hits = search_values(str(status_val), columns=["incdnt_status_name"], min_score=0.6)
        if hits:
            status_val = hits[0].value
        else:
            hits_any = search_values(str(status_val), min_score=0.6)
            if hits_any:
                status_val = hits_any[0].value

    if source_val:
        hits = search_values(str(source_val), columns=["src_type_lvl_2_name"], min_score=0.6)
        if hits:
            source_val = hits[0].value
        else:
            hits_any = search_values(str(source_val), min_score=0.6)
            if hits_any:
                source_val = hits_any[0].value

    if risk_profile_val:
        # Search in risk_profile_id first
        hits_id = search_values(str(risk_profile_val), columns=["risk_profile_id"], min_score=0.6)
        if hits_id:
            risk_profile_val = hits_id[0].value
            risk_profile_col = "risk_profile_id"
        else:
            hits_name = search_values(str(risk_profile_val), columns=["risk_profile_name"], min_score=0.6)
            if hits_name:
                risk_profile_val = hits_name[0].value
                risk_profile_col = "risk_profile_name"
            else:
                hits_any = search_values(str(risk_profile_val), min_score=0.6)
                if hits_any:
                    risk_profile_val = hits_any[0].value
                    risk_profile_col = hits_any[0].column.split(".")[-1]

    if process_val:
        hits = search_values(str(process_val), columns=["process_lvl_4_name"], min_score=0.6)
        if hits:
            process_val = hits[0].value
        else:
            hits_any = search_values(str(process_val), min_score=0.6)
            if hits_any:
                process_val = hits_any[0].value

    allowed = SKILL_ALLOWED_PARAMS.get(skill_id, set())
    
    if tb_val:
        if "tb_filter" in allowed:
            params["tb_filter"] = tb_val
        elif "additional_sql_filter" in allowed:
            params["additional_sql_filter"] = f"org_struct_lvl_3_name = '{tb_val}'"
            
    if block_val:
        if "block_filter" in allowed:
            params["block_filter"] = block_val
        elif "additional_sql_filter" in allowed:
            existing = params.get("additional_sql_filter")
            if existing:
                params["additional_sql_filter"] = f"({existing}) AND funct_block_lvl_3_name = '{block_val}'"
            else:
                params["additional_sql_filter"] = f"funct_block_lvl_3_name = '{block_val}'"
            
    if status_val:
        if "status_filter" in allowed:
            params["status_filter"] = status_val
        elif "additional_sql_filter" in allowed:
            existing = params.get("additional_sql_filter")
            if existing:
                params["additional_sql_filter"] = f"({existing}) AND incdnt_status_name = '{status_val}'"
            else:
                params["additional_sql_filter"] = f"incdnt_status_name = '{status_val}'"

    if process_val:
        if "additional_sql_filter" in allowed:
            existing = params.get("additional_sql_filter")
            clause = f"process_lvl_4_name = '{process_val}'"
            if existing:
                params["additional_sql_filter"] = f"({existing}) AND {clause}"
            else:
                params["additional_sql_filter"] = clause

    if source_val:
        if "additional_sql_filter" in allowed:
            existing = params.get("additional_sql_filter")
            if "\u043e\u0431\u0440\u0430\u0449\u0435\u043d" in str(source_val).lower():
                clause = (
                    "(incdnt_detection_person_name = '\u041a\u043b\u0438\u0435\u043d\u0442' "
                    "OR src_type_lvl_2_name LIKE '%\u043e\u0431\u0440\u0430\u0449\u0435\u043d\u0438%' "
                    "OR incdnt_source_name LIKE '%\u043a\u043b\u0438\u0435\u043d\u0442%')"
                )
            else:
                clause = f"src_type_lvl_2_name = '{source_val}'"
            if existing:
                params["additional_sql_filter"] = f"({existing}) AND {clause}"
            else:
                params["additional_sql_filter"] = clause

    if risk_profile_val and risk_profile_col:
        if risk_profile_col in allowed:
            params[risk_profile_col] = risk_profile_val
        elif "additional_sql_filter" in allowed:
            existing = params.get("additional_sql_filter")
            if existing:
                params["additional_sql_filter"] = f"({existing}) AND {risk_profile_col} = '{risk_profile_val}'"
            else:
                params["additional_sql_filter"] = f"{risk_profile_col} = '{risk_profile_val}'"

    # Генерация SQL-подзапросов для числовых денежных фильтров при наличии money-интентов
    user_msg = ""
    if hasattr(ctx, "history") and ctx.history:
        for m_obj in reversed(ctx.history):
            if m_obj.get("role") == "user":
                user_msg = m_obj.get("content", "")
                break

    if user_msg and "additional_sql_filter" in allowed:
        table_registry = getattr(get_data_store(), "tables", {})
        recovery_table = table_registry.get("recovery")
        financial_table = table_registry.get("financial_impact")
        import re
        pattern = r"(\bбольше\b|\bменьше\b|>\s*|<\s*|=)\s*(\d+(?:[\s\.,]\d+)*)\s*(млрд|млн|тыс|руб|коп)?"
        money_matches = re.findall(pattern, user_msg.lower())
        subqueries = []
        for op_str, num_str, unit in money_matches:
            num_clean = num_str.replace(" ", "").replace(",", ".").replace("\xa0", "")
            try:
                val = float(num_clean)
            except ValueError:
                continue
            if unit == "млрд":
                val *= 1_000_000_000
            elif unit == "млн":
                val *= 1_000_000
            elif unit == "тыс":
                val *= 1_000
            op = ">" if ("больше" in op_str or ">" in op_str) else ("<" if ("меньше" in op_str or "<" in op_str) else "=")
            
            is_recovery = any(x in user_msg.lower() for x in ("возмещ", "возврат", "компенс", "страхов"))
            is_direct = any(x in user_msg.lower() for x in ("прям", "прямого", "прямые"))
            
            if is_recovery and recovery_table:
                subq = f"incdnt_id IN (SELECT incdnt_id FROM {recovery_table} GROUP BY incdnt_id HAVING SUM(recovery_rub_amt) {op} {val})"
            elif is_direct and financial_table:
                subq = f"incdnt_id IN (SELECT incdnt_id FROM {financial_table} WHERE fin_impact_type_name = 'Прямая потеря' GROUP BY incdnt_id HAVING SUM(fin_impact_rub_amt) {op} {val})"
            elif financial_table:
                subq = f"incdnt_id IN (SELECT incdnt_id FROM {financial_table} GROUP BY incdnt_id HAVING SUM(fin_impact_rub_amt) {op} {val})"
            else:
                logger.warning("Money filter skipped: backend has no required detail table")
                continue
            subqueries.append(subq)
            
        if subqueries:
            subq_str = " AND ".join(subqueries)
            existing = params.get("additional_sql_filter")
            if existing:
                params["additional_sql_filter"] = f"({existing}) AND ({subq_str})"
            else:
                params["additional_sql_filter"] = subq_str

    # 3. Очистка параметров от не поддерживаемых данным ноутбуком полей (чтобы не спамить ворнинги)
    if allowed:
        params = {k: v for k, v in params.items() if k in allowed}

    emit = getattr(ctx, "emit", None) or emit
    skill_registry = get_skill_registry()
    skill = skill_registry.get(skill_id)
    if skill is None:
        return ToolResult(
            ok=False,
            error=f"Skill {skill_id!r} не найден. Доступны: {_skill_ids()}"
        )
    
    import inspect
    loop = asyncio.get_running_loop()

    if emit:
        def sync_emit(phase):
            payload = {
                "phase": phase.name,
                "label": phase.label,
                "data": phase.data
            }
            if inspect.iscoroutinefunction(emit):
                coro = emit("notebook_phase", payload)
                asyncio.run_coroutine_threadsafe(coro, loop)
            else:
                loop.call_soon_threadsafe(emit, "notebook_phase", payload)
    else:
        sync_emit = lambda _phase: None

    runner = get_runner()
    # Runner синхронный (Papermill блокирующий) — гоним через to_thread
    def _do():
        return runner.run_phased(
            skill_id=skill_id,
            notebook_path=skill.notebook_path,
            params=params or {},
            emit=sync_emit,
        )
    result = await asyncio.to_thread(_do)

    if result.error:
        return ToolResult(
            ok=False,
            error=f"preset {skill_id} упал: {result.error}",
            duration_ms=result.duration_ms,
        )

    # Регистрируем файл + (если есть) dataframe в session state
    output: dict = {"skill_id": skill_id, "skill_title": skill.title}

    if result.excel_path and result.excel_path.exists():
        f = ctx.register_file(
            name=result.excel_filename,
            path=str(result.excel_path),
            size_bytes=result.excel_path.stat().st_size,
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        output["file_id"] = f.file_id
        output["filename"] = f.name
        output["rows"] = result.rows
        output["size"] = result.excel_meta.get("size") if result.excel_meta else None

    # Подгружаем как pandas DF для возможного post-processing и фильтрации
    try:
        # Сначала считываем заголовки Excel для динамического определения ID-колонок и сохранения их строкового типа
        df_headers = pd.read_excel(result.excel_path, nrows=0, engine="openpyxl")
        dtype_dict = {}
        for col in df_headers.columns:
            col_lower = str(col).lower()
            if any(x in col_lower for x in ("id", "sid", "key", "номер", "идентификатор")):
                if any(x in col_lower for x in ("cnt", "sum", "amt", "val", "кол", "кол-во", "сумма")):
                    continue
                dtype_dict[col] = str
        
        df = pd.read_excel(result.excel_path, dtype=dtype_dict, engine="openpyxl")
        
        # Пост-фильтрация по оргструктуре, блоку, статусу (для пресетов, не поддерживающих эти параметры напрямую в Spark)
        def filter_by_value(dataframe, col_keywords, val):
            val_lower = str(val).lower().strip()
            col_keywords_cleaned = [kw.lower().strip() for kw in col_keywords]
            
            # Find all candidate columns matching the keywords
            candidate_cols = []
            for col in dataframe.columns:
                col_lower = str(col).lower()
                if any(kw in col_lower for kw in col_keywords_cleaned):
                    candidate_cols.append(col)
                    
            if not candidate_cols:
                return dataframe
                
            # Sort candidate columns by priority (lvl 3 -> lvl 4 -> lvl 2 -> others)
            candidate_cols = sorted(candidate_cols, key=lambda c: (
                0 if any(x in str(c).lower() for x in ("lvl_3", "lvl3", "уровень 3", "уровень_3"))
                else 1 if any(x in str(c).lower() for x in ("lvl_4", "lvl4", "уровень 4"))
                else 2 if any(x in str(c).lower() for x in ("lvl_2", "lvl2", "уровень 2"))
                else 3
            ))
            
            # 1. Try exact match on any of the candidate columns in priority order
            for col in candidate_cols:
                mask_exact = dataframe[col].astype(str).str.lower().str.strip() == val_lower
                filtered_df = dataframe[mask_exact]
                if not filtered_df.empty:
                    return filtered_df
                    
            # 2. Try word-level matching (excluding division/block noise words)
            words = [w for w in val_lower.split() if w not in ("банк", "блок", "отделение", "филиал", "пао", "сбербанк", "сбер", "банка", "уровень", "дивизион")]
            if not words:
                return dataframe
                
            import re
            patterns = [rf"\b{re.escape(w[:5])}" for w in words]
            
            for col in candidate_cols:
                mask_words = dataframe[col].astype(str).str.lower().apply(
                    lambda x: all(re.search(p, x) is not None for p in patterns)
                )
                filtered_df = dataframe[mask_words]
                if not filtered_df.empty:
                    return filtered_df
                    
            return dataframe

        original_len = len(df)
        
        if tb_val:
            df = filter_by_value(df, ["tb", "bank", "орг", "org_struct_lvl_3_name", "территориальный", "тб"], tb_val)
            
        if block_val:
            df = filter_by_value(df, ["block", "блок", "funct_block", "направление"], block_val)
            
        if status_val:
            df = filter_by_value(df, ["status", "статус", "stts"], status_val)

        if process_val:
            df = filter_by_value(df, ["process", "процесс", "proc"], process_val)

        if source_val:
            df = filter_by_value(df, ["source", "источник", "src"], source_val)

        if risk_profile_val:
            df = filter_by_value(df, ["risk_profile", "профиль", "profile"], risk_profile_val)

        # Дополнительная пост-фильтрация по произвольным заземленным фильтрам (коды ЦПР DRP-, П-, EVE- и т.д.)
        user_msg = ""
        if hasattr(ctx, "history") and ctx.history:
            for m_obj in reversed(ctx.history):
                if m_obj.get("role") == "user":
                    user_msg = m_obj.get("content", "")
                    break

        if user_msg:
            import re
            # 1. Прямой поиск кодов DRP / П- / EVE- в тексте запроса с гибким regex
            drp_codes = re.findall(r"\b(?:DRP[_\s-]?\d+|[Dd][Rr][Pp][_\s-]?\d+|[Пп][_\s-]?\d{2,}|EVE[_\s-]?\d+)\b", user_msg)
            for code_val in drp_codes:
                digits = re.sub(r"\D", "", code_val)
                prefix = re.sub(r"\d", "", code_val).strip().upper()
                
                if digits and prefix:
                    pattern = re.compile(rf"{prefix}[_\s-]*{digits}", re.IGNORECASE)
                else:
                    pattern = re.compile(re.escape(code_val), re.IGNORECASE)

                mask = pd.Series(False, index=df.index)
                for col in df.columns:
                    col_mask = df[col].astype(str).str.contains(pattern, regex=True, na=False)
                    mask = mask | col_mask
                
                filtered_df = df[mask]
                if not filtered_df.empty:
                    logger.info("[run_preset] Direct code filter '%s' matched %d rows", code_val, len(filtered_df))
                    df = filtered_df
                else:
                    logger.warning("[run_preset] Direct code filter '%s' produced 0 rows across %d columns", code_val, len(df.columns))

            # 2. Фильтрация по grounding кандидатам
            try:
                from utils.resolve.grounding import ground_query
                g_hits = ground_query(user_msg)
                for hit in g_hits:
                    col_name = hit.get("column")
                    val_name = hit.get("value") or hit.get("phrase")
                    if col_name and val_name:
                        df = filter_by_value(df, [col_name, col_name.lower(), "cpr", "code", "процесс", "событие"], val_name)
            except Exception as g_err:
                logger.warning(f"[run_preset] grounding post-filter error: {g_err}")

        # Очищаем все денежные колонки и превращаем их в float
        money_cols = []
        for col in df.columns:
            col_lower = str(col).lower()
            if any(k in col_lower for k in ("sum", "amt", "loss", "recovery", "потер", "ущерб", "возмещ", "сумм")):
                if not any(k in col_lower for k in ("id", "sid", "key", "номер", "идентификатор")):
                    money_cols.append(col)

        def safe_to_numeric(series):
            if pd.api.types.is_numeric_dtype(series):
                return series.fillna(0.0)
            cleaned = series.astype(str).str.replace(r"[^\d\.\,\-]", "", regex=True)
            cleaned = cleaned.str.replace(",", ".", regex=False)
            return pd.to_numeric(cleaned, errors="coerce").fillna(0.0)

        for col in money_cols:
            try:
                df[col] = safe_to_numeric(df[col])
            except Exception as e:
                logger.warning("[run_preset] failed to convert money col %s to numeric: %s", col, e)
            
        # Локальный фильтр по денежным порогам
        if user_msg:
            import re
            money_m = re.findall(
                r"(\bбольше\b|\bменьше\b|>\s*|<\s*|=)\s*(\d+(?:[\s\.,]\d+)*)\s*(млрд|млн|тыс|руб|коп)?",
                user_msg.lower()
            )
            for op_str, num_str, unit in money_m:
                num_clean = num_str.replace(" ", "").replace(",", ".").replace("\xa0", "")
                try:
                    val = float(num_clean)
                except ValueError:
                    continue
                if unit == "млрд":
                    val *= 1_000_000_000
                elif unit == "млн":
                    val *= 1_000_000
                elif unit == "тыс":
                    val *= 1_000
                op = ">" if ("больше" in op_str or ">" in op_str) else ("<" if ("меньше" in op_str or "<" in op_str) else "=")
                
                for col in money_cols:
                    try:
                        if op == ">":
                            df = df[df[col] > val]
                        elif op == "<":
                            df = df[df[col] < val]
                        elif op == "=":
                            df = df[df[col] == val]
                    except Exception as e:
                        logger.warning("[run_preset] failed to filter money col %s: %s", col, e)

        # Стандартизация названий колонок
        rename_dict = {}
        for col in df.columns:
            if any(u'\u0400' <= char <= u'\u04FF' for char in str(col)):
                continue
                
            col_lower = str(col).lower()
            if any(x in col_lower for x in ("incdnt_sum", "общая сумма", "сумма последствий")) and not any(x in col_lower for x in ("rec", "возмещ", "возврат")):
                rename_dict[col] = "Общая сумма последствий (руб.)"
            elif any(x in col_lower for x in ("recovery_rub_amt", "recovery_amt", "сумма возмещен")):
                rename_dict[col] = "Сумма возмещений (руб.)"
            elif any(x in col_lower for x in ("recovery", "возмещ", "возврат")):
                if any(x in col_lower for x in ("amt", "sum", "сумма")) and not any(y in col_lower for y in ("id", "sid", "key", "code", "код", "num", "номер", "dt", "dttm", "date", "дата", "type", "тип", "account", "счет", "счёт", "doc", "документ")):
                    rename_dict[col] = "Сумма возмещений (руб.)"
        if rename_dict:
            df = df.rename(columns=rename_dict)
                        
        from utils.query_spec import reorder_columns
        df_cols = list(df.columns)
        sorted_cols = reorder_columns(df_cols)
        if sorted_cols != df_cols:
            df = df[sorted_cols]

        # Recalculate stats and narrative if local pandas filters shrank the dataframe
        if len(df) != original_len:
            try:
                from backend.skills.runners.notebook_runner import _build_stats, _build_narrative
                data_list = df.to_dict(orient="records")
                new_stats = _build_stats(skill_id, len(df), data_list)
                if result.stats:
                    new_stats["duration_ms"] = result.stats.get("duration_ms", 0)
                new_narrative = _build_narrative(skill_id, params, new_stats)
                result.text = new_narrative
                result.stats = new_stats
                logger.info("[run_preset] Recalculated narrative and stats for filtered DataFrame (%d rows)", len(df))
            except Exception as re_err:
                logger.warning("[run_preset] failed to rebuild narrative after filtering: %s", re_err)
            
        from utils.dataframe_ops import prepare_df_for_excel, aggregate_by_incident_id
        # Агрегируем по уникальным incdnt_id перед финальной выгрузкой в Excel
        df = aggregate_by_incident_id(df)
        df = prepare_df_for_excel(df)
        logger.info("[run_preset] Сохраняю финальный Excel файл (%d строк)...", len(df))
        df.to_excel(result.excel_path, index=False)
        logger.info("[run_preset] Excel файл успешно сохранен (%s)", result.excel_path)
        result.rows = len(df)
        if result.excel_path.exists():
            try:
                result.excel_meta["size"] = result.excel_path.stat().st_size
            except Exception:
                pass
        # Также обновляем метаданные в output
        output["rows"] = len(df)
        if "size" in output and result.excel_meta:
            output["size"] = result.excel_meta.get("size")

        meta = ctx.register_dataframe(
            df,
            description=f"Результат preset'а {skill.title} "
                        f"(params: {params})",
            created_by=f"run_preset:{skill_id}",
        )
        output["df_id"] = meta.df_id
    except Exception as e:  # noqa: BLE001
        logger.warning("[run_preset] не смог прочитать xlsx как DF: %s", e)

    # Пробрасываем stats / dossier / followups для UI
    output["stats"] = result.stats or {}
    if result.dossier:
        output["dossier"] = result.dossier
    if result.followups:
        output["followups"] = result.followups
    if result.text:
        output["text"] = result.text
        output["narrative"] = result.text

    # CSV-альтернатива
    if result.csv_path and result.csv_path.exists():
        output["has_csv"] = True

    return ToolResult(
        ok=True,
        output=output,
        summary=f"preset {skill_id}: {result.rows} строк, "
                f"файл {result.excel_filename!r}",
        duration_ms=result.duration_ms,
    )


# Регистрация в каталоге
REGISTRY.register(Tool(
    name="run_preset",
    description=(
        "Запустить один из готовых отчётов (preset notebook через Papermill). "
        "Используй когда задача попадает под один из готовых скриптов: "
        "'ior_period_pao_sberbank_v2', 'report_period_specific_ior_v2', "
        "'vozmeshenie_ior_v2', 'financial_consequences_ior_v2', "
        "'ior_nonfinancial_consequences_v2', 'deleted_ior_v2', "
        "'ior_hypothesis_v2'. "
        "Каждый принимает свой набор параметров (см. описание skill'а)."
    ),
    args_schema={
        "type": "object",
        "properties": {
            "skill_id": {
                "type": "string",
                "description": "ID одного из готовых скриптов",
            },
            "params": {
                "type": "object",
                "description": (
                    "Параметры скрипта. Для любого готового отчета (preset) вы можете передать "
                    "фильтры в params: 'tb_filter' (территориальный банк, например 'Московский банк'), "
                    "'block_filter' (функциональный блок, например 'Блок Риски'), "
                    "'status_filter' (статус, например 'Удален' или 'Утвержден'), "
                    "а также 'incdnt_entry_dt_begin' / 'incdnt_entry_dt_end' (период дат). "
                    "Для report_specific передавайте {'incdnt_sid': 'EVE-NNNNNNN'}."
                ),
            },
        },
        "required": ["skill_id"],
    },
    returns="{file_id, df_id, rows, stats, dossier?, narrative?}",
    run=run_preset,
    category="presets",
))
