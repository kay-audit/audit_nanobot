"""Full manual resource groups inside the shared DuckDB snapshot."""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

META = "__nanobot_meta.manual_snapshots"


class SnapshotUnavailable(RuntimeError):
    code = "subscription_cache_unavailable"


def quoted(name: str) -> str:
    parts = name.split(".")
    if len(parts) != 2 or not all(re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", part) for part in parts):
        raise ValueError("Expected a schema.table resource")
    return ".".join('"' + part + '"' for part in parts)


def exists(conn: Any, table: str) -> bool:
    schema, name = table.split(".")
    return bool(conn.execute("SELECT 1 FROM information_schema.tables WHERE table_schema=? AND table_name=?", [schema, name]).fetchone())


def metadata(conn: Any, group: str) -> dict[str, Any]:
    try:
        row = conn.execute(f"SELECT generation, refreshed_at, resources FROM {quoted(META)} WHERE group_name=?", [group]).fetchone()
        if not row or not row[0] or not row[1]:
            raise ValueError("Missing snapshot metadata")
        resources = json.loads(row[2])
        if not isinstance(resources, dict) or not resources:
            raise ValueError("Empty resource manifest")
        for name, description in resources.items():
            columns = conn.execute(f"DESCRIBE {quoted(name)}").fetchall()
            if [col[0] for col in columns] != description["columns"] or any(col[1] != "VARCHAR" for col in columns):
                raise ValueError("Snapshot columns differ from manifest")
            if conn.execute(f"SELECT COUNT(*) FROM {quoted(name)}").fetchone()[0] != description["row_count"]:
                raise ValueError("Incomplete snapshot resource")
        datetime.fromisoformat(row[1].replace("Z", "+00:00"))
        return {"generation": row[0], "refreshed_at": row[1], "resources": resources}
    except Exception as exc:
        raise SnapshotUnavailable("Manual snapshot is missing, incomplete or unreadable") from exc


def snapshot_tables(conn: Any) -> list[str]:
    if not exists(conn, META):
        return []
    tables = [META]
    for row in conn.execute(f"SELECT resources FROM {quoted(META)}").fetchall():
        tables.extend(json.loads(row[0]))
    return list(dict.fromkeys(tables))


def private_tables(conn: Any) -> set[str]:
    try:
        return set(snapshot_tables(conn))
    except Exception as exc:
        raise SnapshotUnavailable("Private snapshot manifest is unreadable") from exc


def guard_public_sql(conn: Any, sql: str) -> None:
    """Private resources cannot be reached through public SQL or dynamic scans."""
    from sqlglot import exp, parse

    private = private_tables(conn)
    try:
        from lib.services.table_registry import table_registry
        private.update(r.name for r in table_registry.table_resources() if getattr(r, "sync_mode", "") == "manual_snapshot")
    except Exception:
        pass
    if not private:
        return
    for tree in parse(sql, read="duckdb"):
        if tree is None:
            continue
        for table in tree.find_all(exp.Table):
            name = f"{table.db}.{table.name}".lower()
            if (not isinstance(table.this, exp.Identifier) or name in private
                    or table.name.lower() in {p.split(".")[-1] for p in private}):
                raise PermissionError("Private snapshot resources require the internal lookup API")
        if any(node.key.lower() in {"attach", "copy", "export", "create", "command"} for node in tree.walk()):
            raise PermissionError("External access to private snapshots is forbidden")


def read_group(store: Any, group: str, queries: list[tuple[str, list[Any]]]) -> dict[str, Any]:
    with store._lock:
        if store._conn is None:
            raise SnapshotUnavailable("Cache is not ready")
        manifest = metadata(store._conn, group)
        results = []
        for sql, params in queries:
            from sqlglot import exp, parse_one
            referenced = {f"{table.db}.{table.name}" for table in parse_one(sql, read="duckdb").find_all(exp.Table)}
            if not referenced <= set(manifest["resources"]):
                raise SnapshotUnavailable("Lookup references an unpublished resource")
            cur = store._conn.execute(sql, params)
            columns = [col[0] for col in cur.description]
            results.append([dict(zip(columns, row, strict=True)) for row in cur.fetchall()])
        return {"snapshot": manifest, "results": results}


def replace_group(store: Any, group: str, resources: dict[str, tuple[list[str], Any]], *, generation: str) -> None:
    """Replace complete iterators transactionally; caller publishes only on success."""
    with store._lock:
        conn = store._conn
        conn.execute("BEGIN TRANSACTION")
        try:
            manifest = {}
            for name, (columns, batches) in resources.items():
                if not columns or len(set(columns)) != len(columns) or not all(re.fullmatch(r"[a-z_][a-z_0-9]*", c) for c in columns):
                    raise ValueError("Invalid resource columns")
                conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{name.split(".")[0]}"')
                conn.execute(f"CREATE OR REPLACE TABLE {quoted(name)} (" + ",".join('"' + c + '" VARCHAR' for c in columns) + ")")
                count = 0
                for batch in batches:
                    values = []
                    for record in batch:
                        if set(record) != set(columns):
                            raise ValueError("Incomplete snapshot row")
                        values.append([record[c] for c in columns])
                    if values:
                        conn.executemany(f"INSERT INTO {quoted(name)} VALUES (" + ",".join("?" for _ in columns) + ")", values)
                        count += len(values)
                manifest[name] = {"columns": columns, "row_count": count}
            conn.execute('CREATE SCHEMA IF NOT EXISTS "__nanobot_meta"')
            conn.execute(f"CREATE TABLE IF NOT EXISTS {quoted(META)} (group_name VARCHAR PRIMARY KEY, generation VARCHAR, refreshed_at VARCHAR, resources VARCHAR)")
            conn.execute(f"DELETE FROM {quoted(META)} WHERE group_name=?", [group])
            stamp = datetime.now(UTC).isoformat()
            conn.execute(f"INSERT INTO {quoted(META)} VALUES (?,?,?,?)", [group, generation, stamp, json.dumps(manifest, sort_keys=True)])
            conn.execute("COMMIT")
            store._tables = list(dict.fromkeys([*(store._tables or []), *resources, META]))
            store._dirty = True
        except BaseException:
            conn.execute("ROLLBACK")
            raise


def import_snapshot(conn: Any, path: str | Path, *, manual_only: bool = False) -> list[str]:
    """Copy and release a published snapshot; no second persistent working database."""
    path = Path(path)
    if not path.is_file():
        return []
    literal = "'" + str(path).replace("'", "''") + "'"
    conn.execute(f"ATTACH {literal} AS __manual_source (READ_ONLY)")
    try:
        rows = conn.execute("SELECT table_schema, table_name FROM information_schema.tables WHERE table_catalog='__manual_source' AND table_type='BASE TABLE'").fetchall()
        names = [f"{schema}.{table}" for schema, table in rows]
        if manual_only:
            if META not in names:
                return []
            manifests = conn.execute(f"SELECT resources FROM __manual_source.{quoted(META)}").fetchall()
            wanted = {META}
            for row in manifests:
                wanted.update(json.loads(row[0]))
            if not wanted <= set(names):
                raise SnapshotUnavailable("Published manual snapshot is incomplete")
            names = [name for name in names if name in wanted]
        conn.execute("BEGIN TRANSACTION")
        try:
            for name in names:
                conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{name.split(".")[0]}"')
                conn.execute(f"CREATE OR REPLACE TABLE {quoted(name)} AS SELECT * FROM __manual_source.{quoted(name)}")
            if META in names:
                for (group,) in conn.execute(f"SELECT group_name FROM {quoted(META)}").fetchall():
                    metadata(conn, group)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        return names
    finally:
        conn.execute("DETACH __manual_source")


def restore_manual(store: Any) -> None:
    store._manual_restore_failed = False
    path = getattr(store, "_publish_path", "")
    if path and getattr(store, "_cache_path", "") != path:
        try:
            names = import_snapshot(store._conn, path, manual_only=True)
            if names:
                store._tables = list(dict.fromkeys([*(store._tables or []), *names]))
        except Exception:
            store._manual_restore_failed = True
            from loguru import logger
            logger.warning("Manual snapshot unavailable on startup; internal checks will fail closed")
