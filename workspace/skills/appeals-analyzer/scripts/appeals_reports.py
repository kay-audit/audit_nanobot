"""Explicit SQL backend -> Osiris retrieval -> hydration -> Osiris rerank -> report."""
from __future__ import annotations
import asyncio
import json
import logging
import re
import uuid
from datetime import date
from typing import Any, Dict, Optional
import pandas as pd
from openpyxl import Workbook
from openpyxl.utils.dataframe import dataframe_to_rows

from ..utils.greenplum_engine import (fetch_appeals_by_ids, fetch_candidate_ids_by_product,
                                     parse_structured_analytical_request)
from ..utils.intent_router import Intent, route_intent
from ..utils.local_qwen import def_ask_gigachat
from ..utils.session_extract_manager import get_session_extract, set_session_extract
from ..utils.srb_d3 import rerank_via_srb_d3, retrieve_via_srb_d3
from ..utils.osiris_config import SERVICE
from workspace.utils.osiris_runtime import OsirisUnavailableError
from ..utils.appeal_text import appeal_parts
from ..utils.appeals_artifacts import output_directory, register_artifact
from ..utils.pipeline_config import CONFIG
from .appeals_hypothesis import (answer_complaint_details, answer_complaint_dialog,
    answer_complaint_follow_up, classify_complaint_intent, generate_complaint_hypothesis_narrative)

logger = logging.getLogger(__name__)


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
    if value is None or (not isinstance(value, (list, dict, tuple, set)) and pd.isna(value)):
        return ""
    if isinstance(value, (list, dict, tuple, set)):
        value = json.dumps(value, ensure_ascii=False, default=str)
    if not isinstance(value, str):
        return value
    return "".join(ch for ch in value if ord(ch) in (9, 10, 13) or 32 <= ord(ch) <= 0xD7FF or 0xE000 <= ord(ch) <= 0xFFFD or 0x10000 <= ord(ch) <= 0x10FFFF)[:32767]


def export_complaints_excel(final_df: pd.DataFrame, query_title: str, session_id: str) -> Dict[str, Any]:
    """Export accepted appeals as one session-scoped XLSX."""
    target = output_directory(session_id)
    target.mkdir(parents=True, exist_ok=True)

    token = uuid.uuid4().hex[:8]
    name = re.sub(r"[^\w-]", "_", query_title[:30]) or "appeals"
    xlsx = target / f"appeals_{name}_{token}.xlsx"

    safe = final_df.copy()
    for col in safe.columns:
        safe[col] = safe[col].map(_excel_safe_value)


    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "appeals"

    for row in dataframe_to_rows(safe, index=False, header=True):
        worksheet.append(list(row))

    worksheet.freeze_panes = "A2"
    if worksheet.max_row >= 1 and worksheet.max_column >= 1:
        worksheet.auto_filter.ref = worksheet.dimensions

    workbook.save(xlsx)
    register_artifact(xlsx)

    logger.info("Final Excel count=%s", len(safe))
    return {
        "xlsx_path": str(xlsx),
        "name": xlsx.name,
        "count": len(safe),
    }


def _empty(message: str, session_id: str) -> str:
    set_session_extract(session_id, pd.DataFrame(), skill_name="appeals-analyzer",
                        extra={"final_ids": [], "hypothesis": message})
    return message


def select_accepted(scored: pd.DataFrame, *, reranker_input: int | None = None) -> pd.DataFrame:
    """Keep all threshold passes, topping up to report_min_items by score."""
    if "score" not in scored:
        raise RuntimeError("Reranker produced no scores")
    scores = pd.to_numeric(scored["score"], errors="coerce")
    if not scores.between(0, 1).all():
        raise RuntimeError("Reranker produced invalid scores")
    if CONFIG.report_min_items <= 0:
        raise ValueError("report_min_items must be positive")
    ranked = scored.assign(score=scores).sort_values("score", ascending=False, kind="stable")
    ranked = ranked.drop_duplicates("id", keep="first")
    passing_count = int((ranked["score"] > CONFIG.score_threshold).sum())
    target = max(passing_count, CONFIG.report_min_items)
    selected = ranked.head(target).copy().reset_index(drop=True)
    logger.info("Appeals report selection: reranker_input=%s reranker_output=%s above_threshold=%s "
                "score_threshold=%s report_min_items=%s final_selected=%s",
                reranker_input if reranker_input is not None else "unknown", len(scored), passing_count,
                CONFIG.score_threshold, CONFIG.report_min_items, len(selected))
    return selected


def _rows_for_answer(frame):
    rows = []
    for _, row in frame.iterrows():
        description, dialogue = appeal_parts(row)
        rows.append({"id": str(row["id"]), "desc": description,
                     "dialogue": dialogue, "date": str(row.get("date", ""))})
    return rows


async def _search_population(session_id, query, allowed_ids, date_range=None, *, structural_filters=None):
    candidates = await asyncio.to_thread(retrieve_via_srb_d3, session_id, query, allowed_ids)
    if not candidates:
        return pd.DataFrame()
    hydrated = await asyncio.to_thread(fetch_appeals_by_ids, candidates, date_range,
                                      **(structural_filters or {}))
    if hydrated.empty:
        return pd.DataFrame()
    logger.info("Reranker input=%s", len(hydrated))
    scored = await asyncio.to_thread(rerank_via_srb_d3, session_id, query, hydrated)
    logger.info("Reranker output=%s", len(scored))
    if len(scored) != len(hydrated):
        raise RuntimeError(f"Reranker candidate count mismatch: input={len(hydrated)} output={len(scored)}")
    return select_accepted(scored, reranker_input=len(hydrated))


async def run_appeals_report(session_id: str, user_prompt: str = "", filters: Optional[dict] = None) -> str:
    """Run with a request-local backend; filters is retained for call compatibility."""
    if not session_id:
        raise ValueError("Appeals requires an explicit session key")
    session = get_session_extract(session_id)
    if session and route_intent(user_prompt, True) != Intent.NEW_SEARCH:
        try:
            return await _run_follow_up(session_id, user_prompt, session)
        except OsirisUnavailableError:
            logger.exception("Appeals Osiris startup unavailable during follow-up")
            return _osiris_unavailable_message()
    _empty("", session_id)
    try:
        request = parse_structured_analytical_request(user_prompt)
    except ValueError as exc:
        return _empty(str(exc), session_id)
    query = request["query"]
    date_range = (request["date_range"] if request["format"] in {"canonical", "json"}
                  else extract_search_params(query)["date_range"])
    allowed_ids = await asyncio.to_thread(
        fetch_candidate_ids_by_product, request["products"], request["subproducts"],
        request["channels"], date_range,
    )
    if not allowed_ids:
        return _empty("По указанным фильтрам обращений не найдено.", session_id)
    try:
        structural_filters = {key: request[key] for key in ("products", "subproducts", "channels") if request[key]}
        final_df = await _search_population(session_id, query, allowed_ids, date_range,
                                          structural_filters=structural_filters)
    except OsirisUnavailableError:
        logger.exception("Appeals Osiris startup unavailable")
        return _empty(_osiris_unavailable_message(), session_id)
    if final_df.empty:
        return _empty("Релевантные обращения не подтверждены.", session_id)
    export = await asyncio.to_thread(export_complaints_excel, final_df, query, session_id)
    narrative = await generate_complaint_hypothesis_narrative(
        query, final_df, export, total_db_count=len(final_df),
    )
    set_session_extract(session_id, final_df, skill_name="appeals-analyzer", extra={
        "final_ids": final_df["id"].tolist(), "hypothesis": narrative, "export_info": export,
        "structural_filters": structural_filters, "date_range": date_range,
    })
    return narrative + f"\n\nВыгрузка: {export['name']} ({len(final_df)} уникальных обращений)."


def _osiris_unavailable_message() -> str:
    minutes = max(1, round(SERVICE.retry_after_sec / 60))
    return f"В данный момент сервис обработки недоступен. Попробуйте повторить запрос через {minutes} минут."


async def _run_follow_up(session_id: str, prompt: str, session: Dict[str, Any]) -> str:
    final_ids = list(session.get("final_ids", []))
    hypothesis = session.get("hypothesis", "")
    if not final_ids:
        return "Нет сохранённой релевантной выборки. Выполните новый анализ обращений."
    matched = [cid for cid in final_ids
               if re.search(r"(?<![\w-])" + re.escape(cid) + r"(?![\w-])", prompt)]
    if matched:
        frame = await asyncio.to_thread(fetch_appeals_by_ids, matched, session.get("date_range"),
                                        **session.get("structural_filters", {}))
        return await asyncio.to_thread(answer_complaint_details, prompt, _rows_for_answer(frame), hypothesis=hypothesis)
    if classify_complaint_intent(prompt) == "search":
        frame = await _search_population(session_id, prompt, final_ids, session.get("date_range"),
                                         structural_filters=session.get("structural_filters", {}))
        if frame.empty:
            return "Релевантные обращения не подтверждены."
        return await asyncio.to_thread(answer_complaint_follow_up, prompt, _rows_for_answer(frame),
                                       hypothesis=hypothesis, total_session_count=len(final_ids))
    return await asyncio.to_thread(answer_complaint_dialog, prompt, hypothesis=hypothesis,
                                   total_count=len(final_ids))
