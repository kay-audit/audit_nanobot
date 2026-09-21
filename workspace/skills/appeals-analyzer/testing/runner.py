from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Callable

from lib.services.llm_client import call_llm_json
from workspace.utils.session_key import safe_session_key

from .data_generator import load_records
from .llm_search import search, shortlist
from .report import render_report

SESSION_ROOT = Path(__file__).resolve().parents[3] / "data_store" / "cache" / "testing" / "appeals" / "sessions"


def _parse_request(prompt: str) -> tuple[dict, str]:
    try:
        fields = next(csv.reader([prompt], skipinitialspace=True))
    except csv.Error:
        fields = []
    if len(fields) == 4:
        filters = {key: value.strip() for key, value in zip(("prd", "s_prd", "chnl"), fields[:3]) if value.strip()}
        return filters, fields[3].strip()
    return {}, prompt.strip()


def apply_filters(records: list[dict], filters: dict) -> list[dict]:
    result = records
    for key in ("prd", "s_prd", "chnl"):
        value = str(filters.get(key, "")).strip().lower()
        if value:
            accepted = {part.strip() for part in value.split(",") if part.strip()}
            result = [row for row in result if row[key].lower() in accepted]
    low, high = filters.get("date_from"), filters.get("date_to")
    if low:
        result = [row for row in result if row["date"] >= low]
    if high:
        result = [row for row in result if row["date"] <= high]
    return result


def _session_path(session_id: str) -> Path:
    return SESSION_ROOT / f"{safe_session_key(session_id)}.json"


def _write_session(session_id: str, query: str, ids: list[str]) -> None:
    path = _session_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"query": query, "selected_ids": ids}, ensure_ascii=False), encoding="utf-8")


async def run_testing_report(*, session_id: str, user_prompt: str, llm: Callable = call_llm_json) -> str:
    records = load_records()
    requested_id = re.search(r"APPEAL-TEST-\d{3}", user_prompt, re.I)
    session_path = _session_path(session_id)
    if requested_id and session_path.is_file():
        state = json.loads(session_path.read_text(encoding="utf-8"))
        wanted = requested_id.group(0).upper()
        if wanted in state.get("selected_ids", []):
            row = next((item for item in records if item["appeal_id"] == wanted), None)
            if row:
                return f"# {wanted}\n\n{row['date']} · {row['prd']} / {row['s_prd']} · {row['chnl']}\n\n{row['text']}"
    filters, query = _parse_request(user_prompt)
    candidates = apply_filters(records, filters)
    ranked = search(query, shortlist(query, candidates), llm)
    by_id = {row["appeal_id"]: row for row in candidates}
    selected = [{**by_id[item["id"]], "relevance_score": item["score"], "reason": item["reason"]} for item in ranked if item["id"] in by_id]
    _write_session(session_id, query, [row["appeal_id"] for row in selected])
    return render_report(query, selected)
