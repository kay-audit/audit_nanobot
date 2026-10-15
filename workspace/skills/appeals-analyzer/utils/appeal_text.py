"""Canonical online appeal text and identifiers, without ML dependencies."""
from __future__ import annotations

import pandas as pd


def normalize_text(value) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def appeal_parts(row) -> tuple[str, str]:
    description = normalize_text(row.get("req_desc", row.get("short_description", row.get("Короткое описание"))))
    dialogue = normalize_text(row.get("msg_pprb_chat")) or normalize_text(row.get("msg_crm_call"))
    if not dialogue and "msg_pprb_chat" not in row and "msg_crm_call" not in row:
        dialogue = normalize_text(row.get("Транскрибация диалога", row.get("description")))
    return description, dialogue


def canonical_appeal_text(row) -> str:
    return " ".join(value for value in appeal_parts(row) if value)
