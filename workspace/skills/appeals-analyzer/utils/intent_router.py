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
    if re.match(r"анализ обращений\b", text, re.I) or re.search(r"(?m)^(?:Продукт|Подпродукт|Канал|Период|Запрос):", text):
        return True
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            if re.search(r'"request_type"\s*:\s*"appeals_analysis"|"filters"\s*:', text):
                return True
        else:
            if isinstance(payload, dict) and (payload.get("request_type") == "appeals_analysis" or "filters" in payload):
                return True
    if text.startswith(("{", "[")):
        return False
    # A damaged CSV envelope must not silently become an unfiltered search.
    # Ordinary comma-separated topics are not a CSV envelope.
    return (text.startswith('"') and ',' in text) or bool(re.match(
        r'^[^,\n]{0,80},\s*(?:"|,)', text))


def route_intent(prompt: str, has_selection: bool = False) -> Intent:
    if looks_like_new_analytical_request(prompt):
        return Intent.NEW_SEARCH
    text = (prompt or "").casefold().strip()
    if re.search(
        r"\bнов\w*\s+(?:поиск|анализ|выгрузк)|\bпо\s+всем\s+(?:обращени|жалоб)", text
    ):
        return Intent.NEW_SEARCH
    if has_selection and re.search(r"\b(?:id|обращени[еяюи])\s*[#№:]?\s*\d{5,}\b", text):
        return Intent.SPECIFIC_APPEAL
    if has_selection and re.search(r"среди\s+(?:них|этих|выборки|найденных|полученных)|(?:из|в|по)\s+(?:этих|этой|этим|найденным|полученной|предыдущей|сохранённой)\s+(?:обращени|выборк|выгрузк)", text):
        return Intent.FOLLOWUP_SEARCH
    if has_selection and re.search(
        r"\b(?:этот|этому|этого|предыдущ\w*|полученн\w*)\s+отч[её]т|"
        r"\b(?:поясни|объясни|уточни|разъясни)\s+(?:этот\s+)?(?:вывод|гипотез|результат)|"
        r"\b(?:что\s+(?:означает|значит)|как\s+работает)\b", text
    ):
        return Intent.REPORT_QUESTION
    return Intent.NEW_SEARCH
