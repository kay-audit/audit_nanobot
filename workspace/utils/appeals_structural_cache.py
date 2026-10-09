"""Startup-only Appeals projection and production structural population lookup."""
from __future__ import annotations

import os
import sys
import threading
import time
from datetime import date, timedelta
from pathlib import Path


def _startup_message(message: str) -> None:
    # Startup diagnostics must remain visible even with logging disabled/buffered.
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [appeals-snapshot] "
          f"gateway_pid={os.getpid()} {message}", file=sys.stderr, flush=True)


class _StartupProgress:
    """Report the last known operation, including while a blocking call waits."""

    def __init__(self, interval: float = 30.0):
        self.interval = interval
        self.started = time.monotonic()
        self.stage_started = self.started
        self.stage = "starting"
        self.received = 0
        self.written = 0
        self.batch = 0
        self.gp_pid = None
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._watch, name="appeals-snapshot-progress", daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def set_stage(self, stage: str, **values) -> None:
        with self.lock:
            self.stage = stage
            self.stage_started = time.monotonic()
            for key, value in values.items():
                setattr(self, key, value)

    def report(self, event: str) -> None:
        with self.lock:
            now = time.monotonic()
            message = (f"{event}: stage={self.stage}; elapsed={now - self.started:.1f}s; "
                       f"stage_elapsed={now - self.stage_started:.1f}s; gp_pid={self.gp_pid}; "
                       f"batch={self.batch}; received_rows={self.received}; written_rows={self.written}")
        _startup_message(message)

    def _watch(self) -> None:
        while not self.stopped.wait(self.interval):
            self.report("WAIT/PROGRESS (last known state; not proof of GP progress)")

    def __exit__(self, exc_type, exc, traceback):
        self.stopped.set()
        self.thread.join()
        self.report(f"FAILED {exc_type.__name__}: {exc}" if exc_type else "DONE")

STRUCTURAL_TABLE = "main.appeals_structural_2026"
STRUCTURAL_COLUMNS = ("app_row_id", "req_reg_date", "prd", "s_prd", "chnl")
STRUCTURAL_SOURCE_SCHEMA = "s_grnplm_ld_audit_da_project_34"
STRUCTURAL_SOURCE_TABLE = "t_db_oarb_appeals_d3"
STRUCTURAL_SOURCE = f"{STRUCTURAL_SOURCE_SCHEMA}.{STRUCTURAL_SOURCE_TABLE}"
BATCH_SIZE = 50_000


class AppealsStructuralCacheError(RuntimeError):
    """Production population cannot be served; GP structural fallback is forbidden."""


def structural_load_sql() -> str:
    """Consume the externally prepared population without computing eligibility."""
    return f"""SELECT CAST(app_row_id AS VARCHAR) AS app_row_id,
        CAST(req_reg_date AS TIMESTAMP) AS req_reg_date, prd, s_prd, chnl
    FROM "{STRUCTURAL_SOURCE_SCHEMA}"."{STRUCTURAL_SOURCE_TABLE}"
    """


def structural_arrow_schema():
    import pyarrow as pa

    return pa.schema([
        ("app_row_id", pa.string()), ("req_reg_date", pa.timestamp("us")),
        ("prd", pa.string()), ("s_prd", pa.string()), ("chnl", pa.string()),
    ])


def _replace_arrow_batches(store, table: str, schema, batches) -> int:
    """Атомарно пересоздать таблицу из потока Arrow-батчей.

    Живёт здесь, а не в ``lib/services/duckdb_cache_store.py``: общий стор
    не входит в зону ответственности скилла и не должен меняться под него.
    Дубли сохраняются — в отличие от upsert здесь нет вывода ID.
    """
    import pyarrow as pa

    namespace, _, name = table.rpartition(".")

    def quote(value: str) -> str:
        return '"' + value.replace('"', '""') + '"'

    full = f"{quote(namespace)}.{quote(name)}"
    count = 0
    with store._lock:
        store._open_locked()
        conn = store._conn
        conn.execute("BEGIN")
        try:
            conn.execute(f"CREATE SCHEMA IF NOT EXISTS {quote(namespace)}")
            conn.register("_appeals_arrow", pa.Table.from_batches([], schema=schema))
            try:
                conn.execute(f"CREATE OR REPLACE TABLE {full} AS SELECT * FROM _appeals_arrow")
            finally:
                conn.unregister("_appeals_arrow")
            for batch in batches:
                if batch.schema != schema:
                    raise ValueError(f"Arrow schema mismatch for {table}")
                conn.register("_appeals_arrow", batch)
                try:
                    conn.execute(f"INSERT INTO {full} SELECT * FROM _appeals_arrow")
                finally:
                    conn.unregister("_appeals_arrow")
                count += batch.num_rows
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        if store._tables is None:
            store._tables = []
        if table not in store._tables:
            store._tables.append(table)
        store._dirty = True
    return count


def load_structural_cache(store, db_run, *, batch_size: int = BATCH_SIZE) -> int:
    """Use a server cursor on a leased shared-pool connection; never fetchall."""
    import pyarrow as pa

    if batch_size < 1:
        raise ValueError("Appeals structural batch_size must be positive")
    schema = structural_arrow_schema()
    progress = _StartupProgress()

    def load(conn):
        get_pid = getattr(conn, "get_backend_pid", None)
        progress.set_stage("GP connection acquired", gp_pid=get_pid() if callable(get_pid) else None)
        progress.report("CONNECTED")
        previous_autocommit = conn.autocommit
        conn.autocommit = False
        try:
            with conn.cursor(name="appeals_structural_startup") as cursor:
                cursor.itersize = batch_size
                progress.set_stage("GP DECLARE cursor / prebuilt source query")
                progress.report("QUERY START")
                _startup_message("Prebuilt structural source SQL:\n" + structural_load_sql().strip())
                cursor.execute(structural_load_sql())
                progress.set_stage("DuckDB create structural table / acquire store lock")
                progress.report("CURSOR DECLARED (rows not yet fetched)")

                def batches():
                    while True:
                        progress.set_stage("GP FETCH FORWARD / waiting for next batch", batch=progress.batch + 1)
                        fetch_started = time.monotonic()
                        progress.report("FETCH START")
                        rows = cursor.fetchmany(batch_size)
                        progress.set_stage("validate GP projection", received=progress.received + len(rows))
                        progress.report(f"FETCH DONE rows={len(rows)} fetch_elapsed={time.monotonic() - fetch_started:.1f}s")
                        names = tuple(column[0] for column in (cursor.description or ()))
                        if names != STRUCTURAL_COLUMNS:
                            raise AppealsStructuralCacheError(
                                f"Prebuilt GP source {STRUCTURAL_SOURCE} has incorrect projection: "
                                f"expected {STRUCTURAL_COLUMNS}, received {names}"
                            )
                        if not rows:
                            progress.set_stage("DuckDB commit structural table")
                            progress.report("GP EOF")
                            break
                        progress.set_stage("convert five structural columns to Arrow")
                        batch = pa.RecordBatch.from_arrays([
                            pa.array([row[index] for row in rows], type=field.type)
                            for index, field in enumerate(schema)
                        ], schema=schema)
                        progress.set_stage("DuckDB INSERT Arrow batch")
                        insert_started = time.monotonic()
                        yield batch
                        # The generic store resumes this generator after INSERT succeeds.
                        progress.set_stage("DuckDB batch inserted", written=progress.written + len(rows))
                        progress.report(f"INSERT DONE insert_elapsed={time.monotonic() - insert_started:.1f}s")

                return _replace_arrow_batches(store, STRUCTURAL_TABLE, schema, batches())
        except Exception as exc:
            progress.report(f"LOAD FAILED {type(exc).__name__}: {exc}")
            raise
        finally:
            progress.set_stage("GP transaction rollback / restore connection")
            conn.rollback()
            conn.autocommit = previous_autocommit

    try:
        with progress:
            progress.set_stage("waiting for shared DB pool connection")
            _startup_message(
                f"LOAD START source={STRUCTURAL_SOURCE}; "
                f"target=in-memory DuckDB {STRUCTURAL_TABLE}; year=2026; batch_size={batch_size}; "
                f"columns={','.join(STRUCTURAL_COLUMNS)}; prebuilt_source=true; no_text_transfer=true"
            )
            count = db_run(load)
            progress.set_stage("structural table loaded")
            return count
    except Exception as exc:
        raise AppealsStructuralCacheError(
            f"Appeals structural startup load failed from prebuilt GP source {STRUCTURAL_SOURCE}: {exc}. "
            f"Check source existence, SELECT permission and columns {STRUCTURAL_COLUMNS} "
            "(app_row_id must cast to VARCHAR, req_reg_date to TIMESTAMP, prd/s_prd/chnl must be strings). "
            "The source is maintained by external ETL; no fallback is allowed."
        ) from exc


def build_structural_lookup_sql(products=(), subproducts=(), channels=(), date_range=None):
    conditions, params = [], []
    for column, values in (("prd", products), ("s_prd", subproducts), ("chnl", channels)):
        if values:
            conditions.append(f'"{column}" IN ({", ".join(["%s"] * len(values))})')
            params.extend(values)
    if date_range is not None:
        start, end = (date.fromisoformat(value) if value else None for value in date_range)
        if start and end and start > end:
            raise ValueError("date_range start must not be after end")
        if start:
            conditions.append("req_reg_date >= %s")
            params.append(start)
        if end:
            conditions.append("req_reg_date < %s")
            params.append(end + timedelta(days=1))
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    return (
        f"SELECT DISTINCT CAST(app_row_id AS VARCHAR) AS id FROM {STRUCTURAL_TABLE}{where}",
        params,
    )


def validate_structural_schema(conn) -> None:
    columns = conn.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = 'main' AND table_name = 'appeals_structural_2026' "
        "ORDER BY ordinal_position"
    ).fetchall()
    expected = list(zip(STRUCTURAL_COLUMNS, ("VARCHAR", "TIMESTAMP", "VARCHAR", "VARCHAR", "VARCHAR"), strict=True))
    if columns != expected:
        raise AppealsStructuralCacheError(
            "Appeals structural cache main.appeals_structural_2026 is missing or has incorrect schema; restart Gateway"
        )


def open_structural_snapshot(path):
    import duckdb

    if not path or not Path(path).is_file():
        raise AppealsStructuralCacheError(f"Appeals structural snapshot is unavailable: {path}; restart Gateway")
    try:
        conn = duckdb.connect(str(path), read_only=True)
        try:
            validate_structural_schema(conn)
        except BaseException:
            conn.close()
            raise
        return conn
    except Exception as exc:
        raise AppealsStructuralCacheError(f"Appeals structural snapshot cannot be opened: {exc}") from exc


def lookup_structural_ids(path, products=(), subproducts=(), channels=(), date_range=None):
    """Read the required allowed-ID list in chunks, without dict rows or pandas."""
    from lib.utils.duckdb_query import rewrite_duck_sql

    sql, params = build_structural_lookup_sql(products, subproducts, channels, date_range)
    conn = open_structural_snapshot(path)
    try:
        result = conn.execute(rewrite_duck_sql(sql), params)
        ids = []
        while rows := result.fetchmany(BATCH_SIZE):
            ids.extend(row[0] for row in rows if row[0])
        return ids
    except Exception as exc:
        raise AppealsStructuralCacheError(f"Appeals DuckDB structural lookup failed: {exc}") from exc
    finally:
        conn.close()


def prepare_gateway_structural_cache(ctx) -> None:
    """Build and publish before gateway starts accepting production requests."""
    from loguru import logger

    from workspace.utils.skill_runtime_mode import is_testing_runtime

    settings = ctx.settings
    if is_testing_runtime() or not settings.get("gateway", {}).get("appeals_analyzer", {}).get("enable", True):
        _startup_message("SKIPPED: testing runtime or gateway.appeals_analyzer.enable=false")
        return
    if settings.get("skills", {}).get("appeals_analyzer", {}).get("enabled", False):
        raise AppealsStructuralCacheError(
            "Appeals full-table generic sync must stay disabled; use the startup structural loader"
        )
    if ctx.cache_store is None or ctx.sync_service is None:
        raise AppealsStructuralCacheError("Appeals requires the Gateway DuckDB cache and sync service")
    from utils import db

    try:
        path = ctx.cache_store.get_stats().get("publish_path")
        _startup_message(f"START gateway readiness waits for Appeals snapshot; destination={path}")
        count = load_structural_cache(ctx.cache_store, db.run)
        with _StartupProgress() as progress:
            progress.set_stage("publish DuckDB snapshot to disk", received=count, written=count)
            _startup_message(f"PUBLISH START rows={count}; destination={path}; temporary={path}.tmp")
            if not ctx.cache_store.publish(force=True):
                raise AppealsStructuralCacheError("Appeals structural snapshot publication failed")
            path = ctx.cache_store.get_stats().get("publish_path")
            progress.set_stage("open published snapshot read-only / validate schema")
            _startup_message(f"PUBLISH DONE destination={path}; validating five-column schema")
            open_structural_snapshot(path).close()
            progress.set_stage("published snapshot validated")
        readiness = getattr(ctx, "runtime_readiness", None)
        if readiness is not None:
            from lib.services.runtime_health import ComponentStatus

            def check():
                try:
                    open_structural_snapshot(path).close()
                    return ComponentStatus("appeals_structural_cache", True, "UP", "2026 structural snapshot available")
                except AppealsStructuralCacheError as exc:
                    return ComponentStatus("appeals_structural_cache", True, "DOWN", str(exc))

            readiness.register("appeals_structural_cache", check, required=True)
        logger.info("Appeals structural cache published: {} rows, five columns, year 2026", count)
        _startup_message(f"READY rows={count}; table={STRUCTURAL_TABLE}; snapshot={path}")
    except Exception as exc:
        _startup_message(f"STARTUP FAILED: {type(exc).__name__}: {exc}")
        db.shutdown()
        raise AppealsStructuralCacheError(f"Appeals structural cache is not ready: {exc}") from exc
