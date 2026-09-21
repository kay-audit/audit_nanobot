"""Compatibility facade over the shared ``workspace/utils/db.py`` module.

The facade deliberately verifies the imported module origin. Appeals never
owns a connection, pool, DSN resolver, or retry loop; all DB jobs are queued by
the gateway-wide ``utils.db`` runtime.
"""
from __future__ import annotations

from pathlib import Path

from utils import db as _shared_db

_EXPECTED_DB_PATH = Path(__file__).resolve().parents[3] / "utils" / "db.py"
_LOADED_DB_PATH = Path(getattr(_shared_db, "__file__", "")).resolve()
if _LOADED_DB_PATH != _EXPECTED_DB_PATH.resolve():
    raise ImportError(
        "appeals-analyzer requires the shared workspace/utils/db.py module; "
        f"loaded {_LOADED_DB_PATH or '<unknown>'}"
    )

async_execute = _shared_db.async_execute
async_fetch = _shared_db.async_fetch
async_fetchone = _shared_db.async_fetchone
async_fetchval = _shared_db.async_fetchval
configure = _shared_db.configure
execute = _shared_db.execute
fetch = _shared_db.fetch
fetchone = _shared_db.fetchone
fetchval = _shared_db.fetchval
get_stats = _shared_db.get_stats
resolve_dsn = _shared_db.resolve_dsn
run = _shared_db.run
set_pool_config = _shared_db.set_pool_config
shutdown = _shared_db.shutdown
start = _shared_db.start

__all__ = [
    "async_execute",
    "async_fetch",
    "async_fetchone",
    "async_fetchval",
    "configure",
    "execute",
    "fetch",
    "fetchone",
    "fetchval",
    "get_stats",
    "resolve_dsn",
    "run",
    "set_pool_config",
    "shutdown",
    "start",
]
