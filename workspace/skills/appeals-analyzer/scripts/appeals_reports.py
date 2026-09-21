"""Primary analytical flow: structural GP filter -> hybrid BGE -> hydration -> rerank."""
from __future__ import annotations
import asyncio
import json
import logging
import re
import uuid
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional
import pandas as pd

try:
    from ..utils.bge_search_engine import (build_and_cache_small_index,
        clear_small_index, rerank_dataframe, retrieve_hybrid_adaptive,
        search_small_index, select_threshold_or_fallback)
    from ..utils.greenplum_engine import (fetch_appeals_by_ids, fetch_candidate_ids_by_product,
        parse_structured_analytical_request)
    from ..utils.intent_router import Intent, route_intent
    from ..utils.local_qwen import def_ask_gigachat
    from ..utils.session_extract_manager import get_session_extract, set_session_extract
    from .appeals_hypothesis import (answer_complaint_details, answer_complaint_dialog,
        answer_complaint_follow_up, classify_complaint_intent, generate_complaint_hypothesis_narrative)
except ImportError:  # direct script/CLI compatibility
    from utils.bge_search_engine import (build_and_cache_small_index,
        clear_small_index, rerank_dataframe, retrieve_hybrid_adaptive,
        search_small_index, select_threshold_or_fallback)
    from utils.greenplum_engine import (fetch_appeals_by_ids, fetch_candidate_ids_by_product,
        parse_structured_analytical_request)
    from utils.intent_router import Intent, route_intent
    from utils.local_qwen import def_ask_gigachat
    from utils.session_extract_manager import get_session_extract, set_session_extract
    from appeals_hypothesis import (answer_complaint_details, answer_complaint_dialog,
        answer_complaint_follow_up, classify_complaint_intent, generate_complaint_hypothesis_narrative)

logger = logging.getLogger(__name__)
_SKILL_DIR = Path(__file__).resolve().parents[1]


def validate_date_range(value: Any) -> Optional[tuple[str, str]]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    normalized = []
    for item in value:
        text = str(item).strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return None
        try:
            normalized.append(date.fromisoformat(text))
        except ValueError:
            return None
    if normalized[0] > normalized[1]:
        return None
    return normalized[0].isoformat(), normalized[1].isoformat()


def extract_search_params(semantic_query: str) -> Dict[str, Any]:
    """Extract only date range. The original semantic query is never rewritten."""
    fallback = None
    match = re.search(r"\b(20\d{2})\b", semantic_query or "")
    if match:
        fallback = (f"{match.group(1)}-01-01", f"{match.group(1)}-12-31")
    try:
        prompt = 'Извлеки только date_range из текста. JSON: {"date_range":["YYYY-MM-DD","YYYY-MM-DD"]|null}.'
        raw = str(def_ask_gigachat([{"role": "system", "content": prompt}, {"role": "user", "content": semantic_query}]))
        parsed = json.loads(re.search(r"\{.*?\}", raw, re.S).group(0).replace("None", "null"))
        value = parsed.get("date_range")
        validated = validate_date_range(value)
        if validated is not None:
            return {"date_range": validated}
        logger.info("Date extraction returned invalid range; using deterministic fallback: %r", value)
    except Exception as exc:
        logger.info("Date extraction fallback: %s", exc)
    return {"date_range": fallback}


def _excel_safe_value(value: Any) -> Any:
    if value is None or (not isinstance(value, (list, dict)) and pd.isna(value)):
        return ""
    if isinstance(value, (list, dict, tuple, set)):
        value = json.dumps(value, ensure_ascii=False, default=str)
    if not isinstance(value, str):
        return value
    return "".join(ch for ch in value if ord(ch) in (9, 10, 13) or 32 <= ord(ch) <= 0xD7FF or 0xE000 <= ord(ch) <= 0xFFFD or 0x10000 <= ord(ch) <= 0x10FFFF)[:32767]


def export_complaints_excel(final_df: pd.DataFrame, query_title: str) -> Dict[str, Any]:
    target = _SKILL_DIR / "data_store" / "generated_files"; target.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex[:8]; name = re.sub(r"[^\w-]", "_", query_title[:30]) or "appeals"
    xlsx, csv = target / f"appeals_{name}_{token}.xlsx", target / f"appeals_{name}_{token}.csv"
    safe = final_df.copy()
    for col in safe: safe[col] = safe[col].map(_excel_safe_value)
    safe.to_csv(csv, index=False, encoding="utf-8")
    with pd.ExcelWriter(xlsx, engine="openpyxl") as writer:
        safe.to_excel(writer, sheet_name="appeals", index=False)
    logger.info("Final Excel count=%s", len(safe))
    return {"xlsx_path": str(xlsx), "csv_path": str(csv), "name": xlsx.name, "count": len(safe)}


def _empty(message: str, session_id: str) -> str:
    clear_small_index(session_id)
    set_session_extract(session_id, pd.DataFrame(), skill_name="appeals-analyzer", extra={"id_to_text_map": {}, "hypothesis": message})
    return message


async def run_appeals_report(session_id: str, user_prompt: str = "", filters: Optional[dict] = None) -> str:
    session_id = session_id or "webui_session"
    session = get_session_extract(session_id)
    if session and route_intent(user_prompt, True) != Intent.NEW_SEARCH:
        return _run_follow_up(session_id, user_prompt, session)
    clear_small_index(session_id)
    try:
        request = parse_structured_analytical_request(user_prompt)
    except ValueError as exc:
        return _empty(str(exc), session_id)
    products, subproducts, channels, query = (
        request["products"], request["subproducts"], request["channels"], request["query"],
    )
    # Для WEB JSON даты являются явной частью контракта. Legacy CSV сохраняет
    # прежнее извлечение периода из смыслового запроса.
    date_range = (
        request["date_range"]
        if request.get("format") == "json"
        else extract_search_params(query)["date_range"]
    )
    logger.info(
        "Parsed products=%s subproducts=%s channels=%s date=%s",
        products, subproducts, channels, date_range,
    )
    allowed_ids = None
    if products or subproducts or channels:
        allowed_ids = await asyncio.to_thread(
            fetch_candidate_ids_by_product, products, subproducts, channels, date_range,
        )
        logger.info("Product SQL ID count=%s", len(allowed_ids))
        if not allowed_ids:
            return _empty("По указанным продуктам/субпродуктам обращений не найдено.", session_id)
    try:
        rrf_ids = await asyncio.to_thread(retrieve_hybrid_adaptive, query, allowed_ids, date_range)
    except RuntimeError as exc:
        return _empty(f"Невозможно выполнить semantic retrieval: {exc}", session_id)
    if not rrf_ids:
        return _empty("После product/date mask и hybrid retrieval подходящих обращений не найдено.", session_id)
    hydrated = await asyncio.to_thread(fetch_appeals_by_ids, rrf_ids)
    logger.info("RRF=%s hydrated unique=%s", len(rrf_ids), len(hydrated))
    if hydrated.empty:
        return _empty("Greenplum не вернул тексты для hybrid-кандидатов; результат не сформирован.", session_id)
    try:
        scored = await asyncio.to_thread(rerank_dataframe, query, hydrated)
        final_df, fallback = select_threshold_or_fallback(scored)
    except RuntimeError as exc:
        return _empty(f"Невозможно выполнить BGE reranking: {exc}", session_id)
    logger.info("Reranker scored=%s threshold-passed=%s fallback=%s", len(scored), int((scored.score >= .5).sum()), fallback)
    final_df = final_df.drop_duplicates("id", keep="first").reset_index(drop=True)
    export = await asyncio.to_thread(export_complaints_excel, final_df, query)
    id_map = {str(row["id"]): {"id": str(row["id"]), "desc": str(row.get("Короткое описание", row.get("short_description", ""))), "dialogue": str(row.get("Транскрибация диалога", row.get("msg_pprb_chat", row.get("description", "")))), "date": str(row.get("date", ""))} for _, row in final_df.iterrows()}
    await asyncio.to_thread(build_and_cache_small_index, session_id, id_map)
    narrative = await generate_complaint_hypothesis_narrative(query, final_df, export, total_db_count=len(final_df))
    set_session_extract(session_id, final_df, skill_name="appeals-analyzer", extra={"id_to_text_map": id_map, "hypothesis": narrative, "export_info": export, "fallback": fallback})
    return narrative + f"\n\nВыгрузка: {export['xlsx_path']} ({len(final_df)} уникальных обращений)."


def _run_follow_up(session_id: str, prompt: str, session: Dict[str, Any]) -> str:
    mapping, hypothesis = session.get("id_to_text_map", {}), session.get("hypothesis", "")
    matched = [row for key, row in mapping.items() if key in prompt]
    if matched: return answer_complaint_details(prompt, matched, hypothesis=hypothesis)
    if classify_complaint_intent(prompt) == "search":
        rows = search_small_index(session_id, prompt)
        return answer_complaint_follow_up(prompt, rows, hypothesis=hypothesis, total_session_count=len(mapping))
    return answer_complaint_dialog(prompt, hypothesis=hypothesis, total_count=len(mapping))
