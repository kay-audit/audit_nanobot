"""Route explicit new-analysis envelopes before considering saved-selection follow-ups."""
from __future__ import annotations

from enum import Enum
import json
import re


class Intent(str, Enum):
    NEW_SEARCH = "new_search"
    FOLLOWUP_SEARCH = "followup_search"
    SPECIFIC_APPEAL = "specific_appeal"
    REPORT_QUESTION = "report_question"


def looks_like_new_analytical_request(prompt: str) -> bool:
    text = (prompt or "").strip()
    if text and text.splitlines()[0].strip().casefold() == "анализ обращений":
        return True
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            if re.search(r'"request_type"\s*:\s*"appeals_analysis"', text):
                return True
        else:
            if isinstance(payload, dict) and payload.get("request_type") == "appeals_analysis":
                return True
    quoted = r'"(?:[^"]|"")*"'
    return re.fullmatch(rf"\s*{quoted}\s*,\s*{quoted}\s*,\s*{quoted}\s*,\s*{quoted}\s*", text) is not None


def route_intent(prompt: str, has_selection: bool = False) -> Intent:
    if looks_like_new_analytical_request(prompt):
        return Intent.NEW_SEARCH
    text = (prompt or "").casefold().strip()
    if has_selection and re.search(r"\b(?:id|обращени[ея])?\s*\d{5,}\b", text):
        return Intent.SPECIFIC_APPEAL
    if has_selection and any(x in text for x in ("в каких", "среди них", "среди выборки", "найди", "покажи")):
        return Intent.FOLLOWUP_SEARCH
    return Intent.REPORT_QUESTION if has_selection else Intent.NEW_SEARCH
