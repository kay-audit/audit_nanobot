"""Read-only access to SQL Assistant knowledge-base tables.

This is the only runtime module that knows the physical ``sqlagent.kb_*``
tables.  It deliberately depends on the small CacheProvider capability
(``execute_readonly``), not on DuckDB or Greenplum connections.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

KB_TABLES = "sqlagent.kb_tables"
KB_COLUMNS = "sqlagent.kb_columns"
KB_EXAMPLES = "sqlagent.kb_examples"
_CORPUS_TABLE = {"tables": KB_TABLES, "columns": KB_COLUMNS, "examples": KB_EXAMPLES}
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class KbStoreError(RuntimeError):
    """Controlled KB/cache failure suitable for a structured tool response."""

    def __init__(self, message: str, *, code: str = "not_ready") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class KbQueryResult:
    rows: list[dict[str, Any]]
    columns: list[str]


class KbStore:
    """Parameterized KB queries over an injected cache provider."""

    def __init__(self, provider: Any) -> None:
        if provider is None or not callable(getattr(provider, "execute_readonly", None)):
            raise KbStoreError("SQL Assistant cache provider is not injected")
        self.provider = provider

    def _query(self, sql: str, params: Sequence[Any] | None = None, *, max_rows: int = 1000) -> KbQueryResult:
        result = self.provider.execute_readonly(sql, list(params or ()), max_rows=max_rows)
        if not isinstance(result, Mapping):
            raise KbStoreError("Cache provider returned an invalid response", code="cache_error")
        if result.get("error"):
            message = str(result["error"])
            code = "not_ready" if "not ready" in message.lower() or "does not exist" in message.lower() else "cache_error"
            raise KbStoreError(message, code=code)
        columns = [str(c) for c in result.get("columns", [])]
        rows: list[dict[str, Any]] = []
        for row in result.get("rows", []):
            if isinstance(row, Mapping):
                rows.append(dict(row))
            else:
                rows.append(dict(zip(columns, row)))
        return KbQueryResult(rows, columns)

    @staticmethod
    def _ids(values: Iterable[Any]) -> list[str]:
        return list(dict.fromkeys(str(v) for v in values if v is not None and str(v) != ""))

    def _by_ids(self, table: str, ids: Iterable[Any], *, max_rows: int = 1000) -> list[dict[str, Any]]:
        values = self._ids(ids)
        if not values:
            return []
        marks = ",".join("?" for _ in values)
        return self._query(f"SELECT * FROM {table} WHERE CAST(id AS VARCHAR) IN ({marks}) ORDER BY id", values, max_rows=max_rows).rows

    def tables_by_ids(self, ids: Iterable[Any]) -> list[dict[str, Any]]:
        return self._by_ids(KB_TABLES, ids)

    def tables_by_group_keys(self, group_keys: Iterable[str]) -> list[dict[str, Any]]:
        values = self._ids(group_keys)
        if not values:
            return []
        marks = ",".join("?" for _ in values)
        return self._query(f"SELECT * FROM {KB_TABLES} WHERE group_key IN ({marks}) ORDER BY table_name", values).rows

    def examples_by_ids(self, ids: Iterable[Any]) -> list[dict[str, Any]]:
        return self._by_ids(KB_EXAMPLES, ids)

    def columns_by_ids(self, ids: Iterable[Any]) -> list[dict[str, Any]]:
        values = self._ids(ids)
        if not values:
            return []
        marks = ",".join("?" for _ in values)
        sql = (
            f"SELECT c.*, t.table_name AS table_name FROM {KB_COLUMNS} AS c "
            f"LEFT JOIN {KB_TABLES} AS t ON t.id = c.table_id "
            f"WHERE CAST(c.id AS VARCHAR) IN ({marks}) ORDER BY c.id"
        )
        return self._query(sql, values).rows

    def corpus_by_ids(self, corpus: str, ids: Iterable[Any]) -> list[dict[str, Any]]:
        if corpus == "columns":
            return self.columns_by_ids(ids)
        table = _CORPUS_TABLE.get(corpus)
        if not table:
            raise ValueError("corpus must be tables, columns, or examples")
        return self._by_ids(table, ids)

    def columns_for_table(self, table_id: Any) -> list[dict[str, Any]]:
        return self.columns_for_tables([table_id])

    def columns_for_tables(self, table_ids: Iterable[Any], *, limit: int = 10000) -> list[dict[str, Any]]:
        values = self._ids(table_ids)
        if not values:
            return []
        marks = ",".join("?" for _ in values)
        sql = (
            f"SELECT c.*, t.table_name AS table_name FROM {KB_COLUMNS} AS c "
            f"LEFT JOIN {KB_TABLES} AS t ON t.id = c.table_id "
            f"WHERE CAST(c.table_id AS VARCHAR) IN ({marks}) "
            f"ORDER BY c.table_id, c.ordinal, c.column_name"
        )
        return self._query(sql, values, max_rows=limit).rows

    def example_lookup_exact(self, kind: str, value: Any) -> list[dict[str, Any]]:
        allowed = {"id", "script_id", "km_id", "file_name", "file_path"}
        if kind not in allowed:
            raise ValueError(f"Unsupported exact lookup: {kind}")
        if kind == "file_path":
            sql = f"SELECT * FROM {KB_EXAMPLES} WHERE lower(file_path) LIKE lower(?) ORDER BY script_id, id"
            param = f"%{value}%"
        elif kind in {"km_id", "file_name"}:
            sql = f"SELECT * FROM {KB_EXAMPLES} WHERE lower(CAST({kind} AS VARCHAR)) = lower(?) ORDER BY script_id, id"
            param = str(value)
        else:
            sql = f"SELECT * FROM {KB_EXAMPLES} WHERE CAST({kind} AS VARCHAR) = ? ORDER BY script_id, id"
            param = str(value)
        return self._query(sql, [param], max_rows=1000).rows

    def examples_for_group_keys(self, group_keys: Iterable[str]) -> list[dict[str, Any]]:
        tables = self.tables_by_group_keys(group_keys)
        table_names = self._ids(row.get("table_name") for row in tables)
        if not table_names:
            return []
        predicates = " OR ".join("lower(CAST(tables AS VARCHAR)) LIKE lower(?)" for _ in table_names)
        params = [f'%"{value}"%' for value in table_names]
        return self._query(f"SELECT * FROM {KB_EXAMPLES} WHERE {predicates} ORDER BY id", params, max_rows=1000).rows

    def corpus_frame(self, corpus: str, *, max_rows: int = 100000) -> list[dict[str, Any]]:
        if corpus == "columns":
            sql = (
                f"SELECT c.*, t.table_name AS table_name FROM {KB_COLUMNS} AS c "
                f"LEFT JOIN {KB_TABLES} AS t ON t.id = c.table_id ORDER BY c.id"
            )
            return self._query(sql, max_rows=max_rows).rows
        table = _CORPUS_TABLE.get(corpus)
        if not table:
            raise ValueError("corpus must be tables, columns, or examples")
        return self._query(f"SELECT * FROM {table} ORDER BY id", max_rows=max_rows).rows

    @staticmethod
    def rows_hash(rows: Iterable[Mapping[str, Any]]) -> str:
        rows = list(rows)
        payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def source_hash(self, corpus: str) -> str:
        """Offline content hash. Runtime freshness checks use source_signature()."""
        return self.rows_hash(self.corpus_frame(corpus))

    def source_signature(self, corpus: str) -> str:
        """Cheap freshness marker: aggregate only, never materialize the corpus."""
        table = _CORPUS_TABLE.get(corpus)
        if not table:
            raise ValueError("corpus must be tables, columns, or examples")
        if corpus == "columns":
            sql = (
                f"SELECT COUNT(*) AS row_count, MAX(c.updated_at) AS max_updated_at, "
                f"(SELECT MAX(t.updated_at) FROM {KB_TABLES} AS t) AS tables_max_updated_at "
                f"FROM {KB_COLUMNS} AS c"
            )
        else:
            sql = f"SELECT COUNT(*) AS row_count, MAX(updated_at) AS max_updated_at FROM {table}"
        rows = self._query(sql, max_rows=1).rows
        if not rows:
            raise KbStoreError(f"Knowledge-base corpus {corpus!r} is not ready")
        row = rows[0]
        signature = {"row_count": int(row.get("row_count") or 0), "max_updated_at": str(row.get("max_updated_at") or "")}
        if corpus == "columns":
            signature["tables_max_updated_at"] = str(row.get("tables_max_updated_at") or "")
        return json.dumps(
            signature,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def current_source_signature(self, corpus: str) -> str:
        return self.source_signature(corpus)

    def schema_for_tables(self, table_ids: Iterable[Any]) -> dict[str, dict[str, str]]:
        tables = self.tables_by_ids(table_ids)
        columns = self.columns_for_tables([row.get("id") for row in tables])
        by_id = {str(row.get("id")): str(row.get("table_name")) for row in tables}
        schema: dict[str, dict[str, str]] = {name: {} for name in by_id.values()}
        for col in columns:
            table_name = by_id.get(str(col.get("table_id")))
            if table_name:
                schema[table_name][str(col.get("column_name"))] = str(col.get("data_type") or "UNKNOWN")
        return schema


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value]
    text = str(value).strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(v) for v in parsed]
    except (ValueError, TypeError):
        pass
    return [part.strip() for part in text.split(",") if part.strip()]
