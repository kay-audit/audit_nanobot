"""Explicit request-local backend: shared cache by default, GP only for CLI."""
from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar

import pandas as pd

from .skill_config import PREFIX, SCHEMA, build_cache_provider, source_years

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


class SharedCacheStore:
    def __init__(self, provider=None, *, wait_seconds=None, poll_interval=1.0):
        self.provider = provider if provider is not None else build_cache_provider()
        self.lock = threading.Lock()
        wait = float(os.environ.get("APPEALS_CACHE_WAIT_SECONDS", "60")
                     if wait_seconds is None else wait_seconds)
        if wait < 0 or poll_interval <= 0:
            raise ValueError("Invalid Appeals cache wait settings")
        deadline = time.monotonic() + wait
        while not self.provider.open_cache():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("Shared Gateway DuckDB snapshot is unavailable; wait for initial sync.")
            time.sleep(min(poll_interval, remaining))
        required = {
            "appeal": ("app_row_id", "cust_epk_id", "req_reg_date", "grp", "prd", "s_prd",
                       "chnl", "kanal_reg", "subj", "s_subj", "req_cons_res_val", "req_desc"),
            "appeal_dialogs": ("app_row_id", "msg_pprb_chat", "msg_crm_call"),
            "appeal_task": ("app_row_id", "task_answer", "task_answer_full"),
        }
        for year in source_years():
            for kind, columns in required.items():
                projection = ", ".join(f'"{column}"' for column in columns)
                self.query_sql(f'SELECT {projection} FROM "{SCHEMA}"."{PREFIX}{kind}_{year}" LIMIT 0')

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
