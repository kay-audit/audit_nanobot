"""Refresh both private D1 dictionaries in the shared cache with the gateway stopped.

python -m tools.refresh_d1_subscriptions --profile prod --gp --gateway-stopped
python -m tools.refresh_d1_subscriptions --snapshot PATH --offline-json PACKAGE --gateway-stopped
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from lib.services.manual_snapshots import import_snapshot, metadata, quoted, replace_group

SCHEMA = "s_grnplm_ld_audit_da_sandbox_oarb"
GROUP = "d1_subscriptions"
RESOURCES = {
    f"{SCHEMA}.dvb_kav_user_subs": ["ipa_login", "sudir_login", "schema_name", "sub_alias"],
    f"{SCHEMA}.dvb_kav_subs_list": ["meta_code", "sub_alias", "sub_name"],
}


def rows_digest(rows: list[dict]) -> str:
    return hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def chunks(rows: list[dict]):
    for start in range(0, len(rows), 5000):
        yield rows[start:start + 5000]


def offline_resources(path: Path) -> dict:
    package = json.loads(path.read_text(encoding="utf-8"))
    if package.get("format_version") != 1 or package.get("complete") is not True or set(package.get("tables", {})) != set(RESOURCES):
        raise ValueError("A complete version-1 package containing both dictionaries is required")
    resources = {}
    for name, columns in RESOURCES.items():
        item = package["tables"][name]
        rows = item["rows"]
        if item["columns"] != columns or not isinstance(rows, list) or len(rows) != item["row_count"] or rows_digest(rows) != item["sha256"]:
            raise ValueError("Offline package count, columns or checksum mismatch")
        resources[name] = (columns, chunks(rows))
    return resources


@contextmanager
def gp_resources(args: Any):
    from workspace.skills.sql_assistant.scripts._pg_admin import connect
    conn = connect(args)
    try:
        conn.set_session(readonly=True, isolation_level="REPEATABLE READ")
        def batches(name, columns):
            with conn.cursor() as counter:
                counter.execute(f"SELECT COUNT(*) FROM {quoted(name)}")
                expected = int(counter.fetchone()[0])
            count = 0
            with conn.cursor(name="d1_" + uuid.uuid4().hex) as cursor:
                cursor.itersize = 5000
                cursor.execute("SELECT " + ",".join('"' + c + '"' for c in columns) + " FROM " + quoted(name))
                while rows := cursor.fetchmany(5000):
                    count += len(rows)
                    yield [dict(zip(columns, row, strict=True)) for row in rows]
            if count != expected:
                raise ValueError("Incomplete GP snapshot read")
        yield {name: (columns, batches(name, columns)) for name, columns in RESOURCES.items()}
    finally:
        conn.rollback()
        conn.close()


def new_store(path: Path):
    from lib.services.duckdb_cache_store import DuckDbCacheStore
    if "path" in inspect.signature(DuckDbCacheStore.open).parameters:
        from lib.services.cache_ownership import CacheAccessMode
        store = DuckDbCacheStore.open(path="", mode=CacheAccessMode.READ_WRITE)
        store._publish_path = str(path)
        if not store.connect():
            raise RuntimeError("Cannot initialize shared cache storage")
    else:
        store = DuckDbCacheStore(cache_path="", publish_path=str(path))
        if not store.open():
            raise RuntimeError("Cannot initialize shared cache storage")
    return store


def fingerprint(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while data := stream.read(1024 * 1024):
            digest.update(data)
    return digest.hexdigest()


def refresh(path: Path, resources: dict) -> dict:
    """Caller must stop all gateway owners before entering this operation."""
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + ".manual-refresh.lock")
    with lock.open("x", encoding="utf-8") as handle:
        handle.write(str(os.getpid()))
    store = None
    try:
        before = fingerprint(path)
        store = new_store(path)
        with store._lock:
            store._tables = import_snapshot(store._conn, path)
        if set(resources) != set(RESOURCES):
            raise ValueError("Both D1 dictionaries are required")
        replace_group(store, GROUP, resources, generation=uuid.uuid4().hex)
        result = metadata(store._conn, GROUP)
        if fingerprint(path) != before:
            raise RuntimeError("Shared snapshot changed concurrently; publication cancelled")
        previous_umask = os.umask(0o077)
        try:
            if not store.publish(force=True):
                raise RuntimeError("Snapshot publication failed; previous published file retained")
        finally:
            os.umask(previous_umask)
        return result
    finally:
        if store is not None:
            store.close()
        lock.unlink(missing_ok=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--profile", choices=("prod", "test"))
    parser.add_argument("--gateway-stopped", action="store_true", required=True)
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument("--gp", action="store_true")
    sources.add_argument("--offline-json", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.snapshot is None:
            from workspace.skills.sql_assistant.scripts._pg_admin import initialize_settings
            initialize_settings(args)
            from config import SETTINGS
            from lib.core.application_context import resolve_publish_path
            root = Path(__file__).resolve().parents[1]
            args.snapshot = Path(resolve_publish_path(root / "workspace", SETTINGS.get("gateway", {}).get("cache", {})))
        if args.gp:
            with gp_resources(args) as resources:
                result = refresh(args.snapshot, resources)
        else:
            result = refresh(args.snapshot, offline_resources(args.offline_json))
        print(json.dumps({"status": "published", "snapshot": str(args.snapshot), **result}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "refresh_failed", "error_type": type(exc).__name__,
                          "message": "Refresh failed; previous published snapshot retained. Check source completeness, permissions and gateway shutdown."}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
