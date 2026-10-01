from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_TABLE = re.compile(r"^[A-Za-z0-9_]+\.[A-Za-z0-9_]+$")


def quote_table(value: str) -> str:
    if not _TABLE.fullmatch(value):
        raise ValueError(f"Unsafe schema.table identifier: {value!r}")
    return ".".join(f'"{part}"' for part in value.split("."))


class DuckDbProvider:
    """Explicit offline provider; the database path always comes from CLI."""
    def __init__(self, path: str) -> None:
        import duckdb
        self.connection = duckdb.connect(path, read_only=True)

    def execute_readonly(self, sql: str, params=None, max_rows: int = 1000):
        try:
            cur = self.connection.execute(sql, list(params or ()))
            cols = [item[0] for item in cur.description or ()]
            return {"columns": cols, "rows": cur.fetchmany(max_rows)}
        except Exception as exc:
            return {"error": str(exc)}


class StaticProvider:
    def execute_readonly(self, *_args, **_kwargs):
        return {"error": "KB is not configured for this static-only command"}


class PostgresProvider:
    """Explicit offline/admin provider; never used by gateway tools."""
    def __init__(self, dsn: str) -> None:
        import psycopg2
        self.connection = psycopg2.connect(dsn)

    def execute_readonly(self, sql: str, params=None, max_rows: int = 1000):
        try:
            # KbStore uses DuckDB '?' placeholders; translate only placeholders,
            # never identifiers or user text.
            translated = sql.replace("?", "%s")
            with self.connection.cursor() as cur:
                cur.execute(translated, list(params or ()))
                cols = [item.name for item in cur.description or ()]
                return {"columns": cols, "rows": cur.fetchmany(max_rows)}
        except Exception as exc:
            self.connection.rollback()
            return {"error": str(exc)}


def load_state(path: str | None) -> dict[str, Any]:
    if not path or not Path(path).exists(): return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_state(path: str | None, state: dict[str, Any]) -> None:
    if path: Path(path).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
