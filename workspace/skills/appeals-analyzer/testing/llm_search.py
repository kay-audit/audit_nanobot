from __future__ import annotations

import json
import re
from typing import Callable

from lib.services.llm_client import call_llm_json


def shortlist(query: str, records: list[dict], limit: int = 60) -> list[dict]:
    terms = {word for word in re.findall(r"[а-яa-z]{4,}", query.lower())}
    scored = []
    for row in records:
        text = " ".join(str(value) for value in row.values()).lower()
        scored.append((sum(term in text for term in terms), row))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["appeal_id"]))
    positive = [row for score, row in scored if score > 0]
    return (positive or [row for _, row in scored])[:limit]


def validate_results(payload: dict | None, candidate_ids: set[str]) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    output = []
    seen = set()
    for item in payload.get("results", []):
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id", ""))
        if item_id not in candidate_ids or item_id in seen:
            continue
        seen.add(item_id)
        output.append({
            "id": item_id,
            "score": max(0.0, min(1.0, float(item.get("score", 0.0)))),
            "reason": str(item.get("reason", "Релевантно запросу"))[:500],
        })
    return output


def search(query: str, candidates: list[dict], llm: Callable = call_llm_json) -> list[dict]:
    all_results = []
    for offset in range(0, len(candidates), 25):
        batch = candidates[offset:offset + 25]
        compact = [{"id": r["appeal_id"], "prd": r["prd"], "s_prd": r["s_prd"], "chnl": r["chnl"], "text": r["text"]} for r in batch]
        response = llm([
            {"role": "system", "content": "Rank only relevant existing appeal IDs. Return JSON {\"results\":[{\"id\":...,\"score\":0..1,\"reason\":...}]}. Never invent IDs."},
            {"role": "user", "content": json.dumps({"query": query, "appeals": compact}, ensure_ascii=False)},
        ], max_tokens=1800, temperature=0.0)
        all_results.extend(validate_results(response, {r["appeal_id"] for r in batch}))
    all_results.sort(key=lambda item: (-item["score"], item["id"]))
    return all_results[:50]
