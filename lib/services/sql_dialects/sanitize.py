from __future__ import annotations

import re
from typing import Any

_DT_FUNCS = "to_date|to_timestamp|date_format|unix_timestamp|from_unixtime"
_DT_CALL_RE = re.compile(
    rf"\b({_DT_FUNCS})\s*\(\s*((?:'[^']*'|\"[^\"]*\"|\([^()]*(?:\([^()]*\)[^()]*)*\)|[^,()'\"])*?)\s*,\s*(['\"])([^'\"]*)\3\s*\)",
    re.IGNORECASE,
)


def extract_sql(text: Any) -> str:
    """Extract a SQL markdown block without changing SQL inside the block."""
    value = "" if text is None else str(text)
    match = re.search(r"```\s*sql\s*\r?\n(.*?)```", value, re.IGNORECASE | re.DOTALL)
    return match.group(1) if match else value.strip()


def _fix_pattern(value: str) -> str:
    value = re.sub(r"(?<![A-Za-z])HH24(?![A-Za-z])", "HH", value)
    value = re.sub(r"(?<![A-Za-z])MI(?![A-Za-z])", "mm", value)
    for old, new in (("YYYY", "yyyy"), ("YY", "yy"), ("DD", "dd"), ("SS", "ss")):
        char = old[0]
        value = re.sub(rf"(?<!{char}){old}(?!{char})", new, value)
    return value


def normalize_datetime_patterns(sql: str) -> str:
    def replace(match: re.Match[str]) -> str:
        fn, arg, quote, pattern = match.groups()
        return f"{fn}({normalize_datetime_patterns(arg)}, {quote}{_fix_pattern(pattern)}{quote})"
    return _DT_CALL_RE.sub(replace, sql or "")


def sanitize_sql(text: Any, *, dialect: str = "spark") -> str:
    sql = extract_sql(text)
    if dialect.lower() == "spark":
        sql = normalize_datetime_patterns(sql)
    return sql

