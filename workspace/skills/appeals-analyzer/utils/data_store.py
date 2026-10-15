"""Request-local population backend; hydration always uses the shared GP pool."""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar

import pandas as pd

from .skill_config import build_cache_provider, source_years

_backend: ContextVar[str] = ContextVar("appeals_backend", default="cache")
_store = None
_store_lock = threading.Lock()


@contextmanager
def backend_scope(name: str):
    if name not in {"cache", "greenplum"}:
        raise ValueError(f"Unsupported Appeals backend: {name}")
    token = _backend.set(name)
    try:
        yield
    finally:
        _backend.reset(token)


def configured_years() -> list[int]:
    if _backend.get() == "greenplum":
        return [2026]
    return source_years()


def uses_structural_cache() -> bool:
    return _backend.get() == "cache"


def fetch_structural_ids(products=(), subproducts=(), channels=(), date_range=None):
    from workspace.utils.appeals_structural_cache import (
        AppealsStructuralCacheError,
        lookup_structural_ids,
    )

    from .skill_config import structural_snapshot_path

    try:
        return lookup_structural_ids(structural_snapshot_path(), products, subproducts, channels, date_range)
    except Exception as exc:
        raise AppealsStructuralCacheError(f"Appeals production structural cache failed: {exc}") from exc


class SharedCacheStore:
    def __init__(self, provider=None, *, wait_seconds=None, poll_interval=1.0):
        self.provider = provider if provider is not None else build_cache_provider()
        self.lock = threading.Lock()
        wait = float(0 if wait_seconds is None else wait_seconds)
        if wait < 0 or poll_interval <= 0:
            raise ValueError("Invalid Appeals cache wait settings")
        deadline = time.monotonic() + wait
        while not self.provider.open_cache():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("Shared Gateway DuckDB snapshot is unavailable; wait for initial sync.")
            time.sleep(min(poll_interval, remaining))
        from workspace.utils.appeals_structural_cache import (
            STRUCTURAL_COLUMNS,
            AppealsStructuralCacheError,
        )

        metadata = self.query_sql(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'main' AND table_name = 'appeals_structural_2026' "
            "ORDER BY ordinal_position"
        )
        expected = list(zip(STRUCTURAL_COLUMNS, ("VARCHAR", "TIMESTAMP", "VARCHAR", "VARCHAR", "VARCHAR"), strict=True))
        if list(metadata.itertuples(index=False, name=None)) != expected:
            raise AppealsStructuralCacheError(
                "Appeals structural cache main.appeals_structural_2026 is missing or has incorrect schema; restart Gateway"
            )

    def query_sql(self, sql, params=None):
        with self.lock:
            result = self.provider.query_sql(sql, list(params) if params is not None else None)
        if result.get("status") != "success":
            raise RuntimeError(f"Shared DuckDB query failed: {result.get('error', 'unknown error')}")
        return pd.DataFrame.from_records(result.get("rows") or [], columns=result.get("columns") or [])


def query_sql(sql, params=None):
    global _store
    if _backend.get() == "greenplum":
        from . import db

        def read(conn):
            with conn.cursor() as cursor:
                cursor.execute(sql, tuple(params) if params else None)
                return pd.DataFrame.from_records(cursor.fetchall(), columns=[item[0] for item in cursor.description])

        return db.run(read)
    with _store_lock:
        if _store is None:
            _store = SharedCacheStore()
        store = _store
    return store.query_sql(sql, params)
