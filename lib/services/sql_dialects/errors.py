from __future__ import annotations

import re
from typing import Any


def map_sql_error(error: Any, *, dialect: str = "spark") -> dict[str, str]:
    text = str(error or "")
    upper = text.upper()
    if "AMBIGUOUS" in upper:
        code = "ambiguous_column"
    elif "UNRESOLVED_COLUMN" in upper or "COLUMN" in upper and ("NOT FOUND" in upper or "UNKNOWN" in upper):
        code = "unknown_column"
    elif "TABLE_OR_VIEW_NOT_FOUND" in upper or "RELATION" in upper and "DOES NOT EXIST" in upper:
        code = "unknown_table"
    elif "PARSE" in upper or "SYNTAX" in upper:
        code = "syntax_error"
    elif re.search(r"SQLSTATE\s*[:=]?\s*42P01", upper):
        code = "unknown_table"
    else:
        code = "validation_error"
    return {"code": code, "message": text, "dialect": dialect}

