from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Callable

from lib.services.llm_client import call_llm_json
from workspace.utils.session_key import safe_session_key

from .data_generator import load_records

SESSION_ROOT = Path(__file__).resolve().parents[3] / "data_store" / "cache" / "testing" / "ior" / "sessions"


def _filter(records: list[dict], prompt: str) -> list[dict]:
    text = prompt.lower()
    result = records
    eve_ids = {item.upper() for item in re.findall(r"eve-test-\d{4}", text, re.I)}
    drps = {item.upper() for item in re.findall(r"drp-test-\d{3}", text, re.I)}
    if eve_ids:
        result = [r for r in result if r["eve_id"] in eve_ids]
    if drps:
        result = [r for r in result if r["drp"] in drps]
    dates = re.findall(r"20\d{2}-\d{2}-\d{2}", text)
    if dates:
        low = min(dates)
        high = max(dates) if len(dates) > 1 else dates[0]
        result = [r for r in result if low <= r["date"] <= high]
    return result


def _session_path(session_id: str) -> Path:
    return SESSION_ROOT / f"{safe_session_key(session_id)}.json"


def _save_session(session_id: str, prompt: str, records: list[dict]) -> None:
    path = _session_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"query": prompt, "selected_ids": [r["eve_id"] for r in records]}, ensure_ascii=False), encoding="utf-8")


def _semantic_candidates(records: list[dict], prompt: str, limit: int = 60) -> list[dict]:
    words = {w for w in re.findall(r"[а-яa-z]{4,}", prompt.lower())}
    scored = []
    for record in records:
        haystack = " ".join(str(v) for v in record.values()).lower()
        score = sum(word in haystack for word in words)
        if score:
            scored.append((score, record))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["eve_id"]))
    return [record for _, record in scored[:limit]] or records[:limit]


def _llm_select(prompt: str, candidates: list[dict], llm: Callable = call_llm_json) -> list[dict]:
    compact = [{"eve_id": r["eve_id"], "date": r["date"], "description": r["description"], "category": r["category"]} for r in candidates]
    response = llm([
        {"role": "system", "content": "Select relevant existing eve_id values. Return JSON: {\"ids\":[...]}. Never invent IDs."},
        {"role": "user", "content": json.dumps({"query": prompt, "candidates": compact}, ensure_ascii=False)},
    ], max_tokens=1200, temperature=0.0)
    valid = {r["eve_id"]: r for r in candidates}
    ids = response.get("ids", []) if isinstance(response, dict) else []
    return [valid[item] for item in ids if item in valid]


async def run_testing_report(*, session_id: str, user_prompt: str, preset_name: str | None = None, llm: Callable = call_llm_json) -> str:
    records = load_records()
    lower = user_prompt.lower()
    session_path = _session_path(session_id)
    if session_path.is_file() and any(marker in lower for marker in ("среди них", "в этой выборке", "из найденных", "а сколько", "а какая")):
        state = json.loads(session_path.read_text(encoding="utf-8"))
        previous = set(state.get("selected_ids", []))
        records = [row for row in records if row["eve_id"] in previous]
    records = _filter(records, user_prompt)
    numeric = any(word in lower for word in ("сколько", "количество", "сумм", "средн", "потер", "возмещ"))
    explicit = bool(re.search(r"(?:eve|drp)-test-", lower)) or bool(re.findall(r"20\d{2}-\d{2}-\d{2}", lower))
    selected = records if numeric or explicit else _llm_select(user_prompt, _semantic_candidates(records, user_prompt), llm)
    _save_session(session_id, user_prompt, selected)
    loss = sum(float(r["financial_loss"]) for r in selected)
    reimb = sum(float(r["reimbursement"]) for r in selected)
    average = loss / len(selected) if selected else 0.0
    evidence = "\n".join(f"- {r['eve_id']} · {r['date']} · {r['description']}" for r in selected[:20]) or "- Совпадений нет"
    return (
        "# Тестовый отчёт по ИОР\n\n"
        f"Найдено: **{len(selected)}**\n\n"
        f"Финансовые потери: **{loss:,.2f}**\n\n"
        f"Возмещение: **{reimb:,.2f}**\n\n"
        f"Средние потери: **{average:,.2f}**\n\n"
        "## Evidence\n" + evidence
    )
