"""Session routing: only a valid temporary four-field request starts a search."""
from enum import Enum
import re


class Intent(str, Enum):
    NEW_SEARCH = "new_search"
    FOLLOWUP_SEARCH = "followup_search"
    SPECIFIC_APPEAL = "specific_appeal"
    REPORT_QUESTION = "report_question"


def route_intent(prompt: str, has_selection: bool = False) -> Intent:
    try:
        try:
            from .greenplum_engine import is_structured_analytical_request
        except ImportError:
            from greenplum_engine import is_structured_analytical_request
        if is_structured_analytical_request(prompt):
            return Intent.NEW_SEARCH
    except Exception:
        pass
    text = (prompt or "").casefold().strip()
    if has_selection and re.search(r"\b(?:id|обращени[ея])?\s*\d{5,}\b", text):
        return Intent.SPECIFIC_APPEAL
    if has_selection and any(x in text for x in ("в каких", "среди них", "среди выборки", "найди", "покажи")):
        return Intent.FOLLOWUP_SEARCH
    return Intent.REPORT_QUESTION if has_selection else Intent.NEW_SEARCH
