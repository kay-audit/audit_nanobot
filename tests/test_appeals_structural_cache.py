"""Real DuckDB/Arrow tests with offline GP cursor and routing contracts."""
from __future__ import annotations

import asyncio
import importlib
import importlib.util
import sys
import threading
import types
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

import duckdb
import pandas as pd
import pyarrow as pa
import pytest

from lib.services.duckdb_cache_store import DuckDbCacheStore
from lib.services.runtime_health import RuntimeReadiness
from workspace.utils import appeals_structural_cache as cache

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "workspace/skills/appeals-analyzer"
PACKAGE = "appeals_structural_contract"
spec = importlib.util.spec_from_file_location(PACKAGE, SKILL / "__init__.py", submodule_search_locations=[str(SKILL)])
package = importlib.util.module_from_spec(spec)
sys.modules[PACKAGE] = package
spec.loader.exec_module(package)
gp = importlib.import_module(f"{PACKAGE}.utils.greenplum_engine")
backend = importlib.import_module(f"{PACKAGE}.utils.data_store")
skill_config = importlib.import_module(f"{PACKAGE}.utils.skill_config")
reports = importlib.import_module(f"{PACKAGE}.scripts.appeals_reports")


class SourceCursor:
    def __init__(self, rows):
        self.rows = iter(rows)
        self.description = None
        self.sizes = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def execute(self, sql):
        self.sql = sql

    def fetchmany(self, size):
        self.sizes.append(size)
        self.description = [(name,) for name in cache.STRUCTURAL_COLUMNS]
        result = []
        for _ in range(size):
            row = next(self.rows, None)
            if row is None:
                break
            result.append(row)
        return result

    def fetchall(self):
        pytest.fail("Startup must use streaming fetchmany")


class SourceConnection:
    def __init__(self, rows):
        self.autocommit = True
        self.stream = SourceCursor(rows)
        self.rollback = Mock()

    def cursor(self, *, name):
        assert name == "appeals_structural_startup"
        assert self.autocommit is False
        return self.stream


def store_at(path):
    return DuckDbCacheStore(publish_path=str(path), tables=["other.rows"])


def test_startup_diagnostics_show_received_and_written_rows(tmp_path, capsys):
    store = store_at(tmp_path / "cache.duckdb")
    source = SourceConnection([("42", datetime(2026, 1, 1), "A", "X", "Chat")])
    source.get_backend_pid = lambda: 12345
    try:
        assert cache.load_structural_cache(store, lambda fn: fn(source), batch_size=1) == 1
        output = capsys.readouterr().err
        assert "gp_pid=12345" in output
        assert "FETCH DONE rows=1" in output
        assert "received_rows=1; written_rows=0" in output
        assert "INSERT DONE" in output
        assert "received_rows=1; written_rows=1" in output
        assert "GP EOF" in output
        assert "prebuilt_source=true; no_text_transfer=true" in output
        assert cache.STRUCTURAL_SOURCE in output
        assert "Eligibility SQL" not in output
    finally:
        store.close()


def test_startup_heartbeat_during_wait_and_cleanup_on_failure(monkeypatch):
    messages = []
    heartbeat_seen = threading.Event()

    def capture(message):
        messages.append(message)
        if message.startswith("WAIT/PROGRESS"):
            heartbeat_seen.set()

    monkeypatch.setattr(cache, "_startup_message", capture)
    progress = cache._StartupProgress(interval=0.01)
    with pytest.raises(RuntimeError, match="GP fetch failed"):
        with progress:
            progress.set_stage("GP FETCH FORWARD / waiting for next batch", gp_pid=12345)
            assert heartbeat_seen.wait(2), "No diagnostic heartbeat during blocking wait"
            raise RuntimeError("GP fetch failed")
    assert not progress.thread.is_alive()
    assert any("stage=GP FETCH FORWARD" in message and "gp_pid=12345" in message for message in messages)
    assert "FAILED RuntimeError: GP fetch failed" in messages[-1]


def test_prebuilt_source_sql_and_projection():
    """Execute the unmodified SELECT in DuckDB; this is not a live GP test."""
    sql = cache.structural_load_sql()
    assert f'"{cache.STRUCTURAL_SOURCE_SCHEMA}"."{cache.STRUCTURAL_SOURCE_TABLE}"' in sql
    for forbidden in ("req_desc", "msg_pprb_chat", "msg_crm_call", "task_answer",
                      "exists", "appeal_dialogs_2026", "40_kaluginvs_anofl_",
                      "distinct", "join", "where", "regexp", "~"):
        assert forbidden not in sql.lower()
    with duckdb.connect() as conn:
        conn.execute(f'CREATE SCHEMA "{cache.STRUCTURAL_SOURCE_SCHEMA}"')
        conn.execute(f"CREATE TABLE {cache.STRUCTURAL_SOURCE} "
                     "(app_row_id BIGINT, req_reg_date DATE, prd VARCHAR, s_prd VARCHAR, chnl VARCHAR)")
        conn.execute(f"INSERT INTO {cache.STRUCTURAL_SOURCE} VALUES "
                     "(1, '2026-01-02', 'A', 'X', 'Chat'), (1, '2026-01-02', 'B', 'Y', 'Office')")
        result = conn.execute(sql)
        assert tuple(item[0] for item in result.description) == cache.STRUCTURAL_COLUMNS
        assert result.fetchall() == [
            ("1", datetime(2026, 1, 2), "A", "X", "Chat"),
            ("1", datetime(2026, 1, 2), "B", "Y", "Office"),
        ]


@pytest.mark.parametrize("problem", ["missing_table", *cache.STRUCTURAL_COLUMNS, "bad_timestamp"])
def test_prebuilt_source_failure_aborts_gateway_without_fallback(tmp_path, monkeypatch, problem):
    """Run startup against an offline SQL source to exercise real SELECT failures."""
    import utils.db as db

    path = tmp_path / "cache.duckdb"
    store = store_at(path)
    source = SourceConnection([])
    statements = []
    with duckdb.connect() as sql_source:
        sql_source.execute(f'CREATE SCHEMA "{cache.STRUCTURAL_SOURCE_SCHEMA}"')
        if problem != "missing_table":
            fields = {"app_row_id": "BIGINT", "req_reg_date": "VARCHAR", "prd": "VARCHAR",
                      "s_prd": "VARCHAR", "chnl": "VARCHAR"}
            fields.pop(problem, None)
            definitions = ', '.join(f'{name} {kind}' for name, kind in fields.items())
            sql_source.execute(f"CREATE TABLE {cache.STRUCTURAL_SOURCE} ({definitions})")
            if problem == "bad_timestamp":
                sql_source.execute(f"INSERT INTO {cache.STRUCTURAL_SOURCE} VALUES (1, 'broken', 'A', 'X', 'Chat')")

        def execute(statement):
            statements.append(statement)
            sql_source.execute(statement)
            # Force evaluation for engines that defer expression errors to fetch.
            sql_source.fetchmany(1)

        source.stream.execute = execute
        monkeypatch.setattr(db, "run", lambda fn: fn(source))
        shutdown = Mock()
        monkeypatch.setattr(db, "shutdown", shutdown)
        monkeypatch.setattr("workspace.utils.skill_runtime_mode.is_testing_runtime", lambda: False)
        published = Mock()
        monkeypatch.setattr(store, "publish", published)
        ctx = types.SimpleNamespace(settings={}, cache_store=store, sync_service=Mock())
        with pytest.raises(cache.AppealsStructuralCacheError, match=cache.STRUCTURAL_SOURCE):
            cache.prepare_gateway_structural_cache(ctx)
        assert statements == [cache.structural_load_sql()]
        published.assert_not_called()
        shutdown.assert_called_once()
        source.rollback.assert_called_once()
        assert source.autocommit is True
        assert not path.exists()
    store.close()


def test_chunked_projection_duplicates_and_republication(tmp_path):
    path = tmp_path / "cache.duckdb"
    store = store_at(path)
    stamp = datetime(2026, 2, 1)
    rows = [("1", stamp, "A", "X", "Chat"), ("1", stamp, "B", "Y", "Office"),
            ("1", stamp, "A", "X", "Chat"), ("2", stamp, "A", "Y", "Office")]
    source = SourceConnection(rows)
    assert cache.load_structural_cache(store, lambda fn: fn(source), batch_size=2) == 4
    assert source.stream.sizes == [2, 2, 2]
    assert source.stream.closed and source.autocommit is True
    source.rollback.assert_called_once()
    assert store.publish(force=True)
    for products, subproducts in ((["A"], ["X"]), (["B"], ["Y"])):
        assert cache.lookup_structural_ids(path, products, subproducts) == ["1"]
    assert set(cache.lookup_structural_ids(path)) == {"1", "2"}
    with duckdb.connect(str(path), read_only=True) as conn:
        cache.validate_structural_schema(conn)
        assert conn.execute(f"SELECT count(*) FROM {cache.STRUCTURAL_TABLE}").fetchone()[0] == 4
    store.upsert_records("other.rows", [{"id": 1, "value": "unrelated"}])
    assert store.publish()
    assert set(cache.lookup_structural_ids(path)) == {"1", "2"}
    store.close()


@pytest.mark.parametrize("columns", [(), cache.STRUCTURAL_COLUMNS[:-1], (*cache.STRUCTURAL_COLUMNS, "req_desc")])
def test_incorrect_source_projection_fails_with_expected_and_actual_columns(tmp_path, columns):
    store = store_at(tmp_path / "cache.duckdb")
    source = SourceConnection([])

    def fetch(size):
        source.stream.description = [(name,) for name in columns]
        return []

    source.stream.fetchmany = fetch
    try:
        with pytest.raises(cache.AppealsStructuralCacheError, match="incorrect projection") as error:
            cache.load_structural_cache(store, lambda fn: fn(source))
        assert cache.STRUCTURAL_SOURCE in str(error.value)
        assert f"expected {cache.STRUCTURAL_COLUMNS}, received {columns}" in str(error.value)
        assert cache.STRUCTURAL_TABLE not in store._tables
        source.rollback.assert_called_once()
    finally:
        store.close()


def test_boolean_and_inclusive_date_semantics(tmp_path):
    path = tmp_path / "cache.duckdb"
    store = store_at(path)
    rows = [("1", datetime(2026, 1, 31, 23, 59), "A", "X", "Chat"),
            ("2", datetime(2026, 1, 2), "B", "Y", "Office"),
            ("3", datetime(2026, 2, 1), "A", "X", "Chat"),
            ("4", datetime(2026, 1, 2), "A", "Z", "Chat")]
    cache.load_structural_cache(store, lambda fn: fn(SourceConnection(rows)), batch_size=2)
    assert store.publish(force=True)
    assert set(cache.lookup_structural_ids(path, ["A", "B"], ["X", "Y"], ["Chat", "Office"],
                                            ("2026-01-01", "2026-01-31"))) == {"1", "2"}
    assert cache.lookup_structural_ids(path, [], [], ["Office"]) == ["2"]
    assert cache.lookup_structural_ids(path, date_range=("2025-01-01", "2025-12-31")) == []
    assert len(cache.lookup_structural_ids(path, [], [], [])) == 4
    store.close()


def test_arrow_bulk_failure_rolls_back_without_deduplication(tmp_path):
    store = store_at(tmp_path / "cache.duckdb")
    schema = cache.structural_arrow_schema()
    good = pa.RecordBatch.from_pylist([dict(zip(cache.STRUCTURAL_COLUMNS,
        ("1", datetime(2026, 1, 1), "A", "X", "Chat")))], schema=schema)
    assert cache._replace_arrow_batches(store, cache.STRUCTURAL_TABLE, schema, [good]) == 1
    bad = pa.RecordBatch.from_pylist([{"unexpected": "text"}])
    with pytest.raises(ValueError, match="schema mismatch"):
        cache._replace_arrow_batches(store, cache.STRUCTURAL_TABLE, schema, [good, bad])
    assert store.query_sql(f"SELECT count(*) AS n FROM {cache.STRUCTURAL_TABLE}")["rows"] == [{"n": 1}]
    store.close()


@pytest.mark.parametrize("problem", ["missing", "schema", "unreadable", "lookup"])
def test_production_cache_failure_never_calls_gp(tmp_path, monkeypatch, problem):
    path = tmp_path / "cache.duckdb"
    forbidden = Mock(side_effect=AssertionError("GP structural fallback"))
    monkeypatch.setattr(gp, "query_sql", forbidden)
    monkeypatch.setattr(skill_config, "structural_snapshot_path", lambda: str(path))
    if problem == "schema":
        with duckdb.connect(str(path)) as conn:
            conn.execute("CREATE TABLE appeals_structural_2026 (app_row_id VARCHAR)")
    elif problem == "unreadable":
        path.write_text("not a DuckDB file")
    elif problem == "lookup":
        fake = Mock()
        fake.execute.side_effect = RuntimeError("infrastructure error")
        monkeypatch.setattr(cache, "open_structural_snapshot", lambda _: fake)
    with backend.backend_scope("cache"), pytest.raises(cache.AppealsStructuralCacheError):
        gp.fetch_candidate_ids_by_product([], [])
    forbidden.assert_not_called()


def test_production_and_standalone_routing(tmp_path, monkeypatch):
    path = tmp_path / "cache.duckdb"
    store = store_at(path)
    cache.load_structural_cache(store, lambda fn: fn(SourceConnection([
        ("42", datetime(2026, 1, 1), "A", "X", "Chat")
    ])))
    assert store.publish(force=True)
    monkeypatch.setattr(skill_config, "structural_snapshot_path", lambda: str(path))
    calls = []
    base = pd.DataFrame([{"source_year": 2026, "id": "42", "app_row_id": "42",
        "_join_app_row_id": "42", "req_reg_date": datetime(2026, 1, 1), "prd": "A", "s_prd": "X", "chnl": "Chat"}])

    def query(statement, params=None):
        assert backend._backend.get() == "greenplum"
        calls.append(statement)
        if " AS source_year" in statement and "a.prd" in statement:
            return base
        if " AS id " in statement:
            return pd.DataFrame({"id": ["42"]})
        return pd.DataFrame()

    monkeypatch.setattr(gp, "query_sql", query)
    for mode in ("cache", "greenplum"):
        calls.clear()
        if mode == "greenplum":
            monkeypatch.setattr(skill_config, "structural_snapshot_path", Mock(side_effect=AssertionError("Standalone read snapshot")))
        with backend.backend_scope(mode):
            assert gp.fetch_candidate_ids_by_product(["A"], ["X"]) == ["42"]
            assert len(calls) == (0 if mode == "cache" else 1)
            result = gp.fetch_appeals_by_ids(["42"], products=["A"], subproducts=["X"])
            assert result.prd.tolist() == ["A"]
            assert backend._backend.get() == mode
        assert len(calls) == (3 if mode == "cache" else 4)
        assert all(cache.STRUCTURAL_SOURCE_TABLE not in statement for statement in calls)
        if mode == "greenplum":
            assert "40_kaluginvs_anofl_appeal_2026" in calls[0]
            assert "req_desc" in calls[0]
    store.close()


def test_hydration_reapplies_filters_before_duplicate_id_aggregation(monkeypatch):
    rows = pd.DataFrame([
        {"source_year": 2026, "id": "42", "app_row_id": "42", "_join_app_row_id": "42",
         "prd": "Р”СЂСѓРіРѕРµ", "s_prd": "Р”СЂСѓРіРѕРµ", "chnl": "Office", "req_reg_date": datetime(2026, 1, 3)},
        {"source_year": 2026, "id": "42", "app_row_id": "42", "_join_app_row_id": "42",
         "prd": "Р‘Р°РЅРєРѕРІСЃРєРёРµ РєР°СЂС‚С‹", "s_prd": "РљСЂРµРґРёС‚РЅС‹Рµ РєР°СЂС‚С‹", "chnl": "Chat", "req_reg_date": datetime(2026, 1, 3)},
    ])
    observed = []

    def query(statement, params=None):
        observed.append(statement)
        if "a.prd" in statement:
            assert "a.prd IN ('Р‘Р°РЅРєРѕРІСЃРєРёРµ РєР°СЂС‚С‹')" in statement
            assert "a.s_prd IN ('РљСЂРµРґРёС‚РЅС‹Рµ РєР°СЂС‚С‹')" in statement
            assert "a.chnl IN ('Chat')" in statement
            assert "a.req_reg_date < DATE '2026-02-01'" in statement
            return rows.loc[rows.prd == "Р‘Р°РЅРєРѕРІСЃРєРёРµ РєР°СЂС‚С‹"]
        assert "prd IN" not in statement and "req_reg_date" not in statement
        return pd.DataFrame()

    monkeypatch.setattr(gp, "query_sql", query)
    result = gp.fetch_appeals_by_ids(["42"], ("2026-01-01", "2026-01-31"),
        products=["Р‘Р°РЅРєРѕРІСЃРєРёРµ РєР°СЂС‚С‹"], subproducts=["РљСЂРµРґРёС‚РЅС‹Рµ РєР°СЂС‚С‹"], channels=["Chat"])
    assert len(observed) == 3
    assert result.prd.tolist() == ["Р‘Р°РЅРєРѕРІСЃРєРёРµ РєР°СЂС‚С‹"]
    assert result.s_prd.tolist() == ["РљСЂРµРґРёС‚РЅС‹Рµ РєР°СЂС‚С‹"]
    monkeypatch.setattr(gp, "query_sql", lambda *args: rows)
    with pytest.raises(RuntimeError, match="violates requested prd"):
        gp.fetch_appeals_by_ids(["42"], products=["Р‘Р°РЅРєРѕРІСЃРєРёРµ РєР°СЂС‚С‹"])


def test_empty_source_publishes_valid_empty_table(tmp_path):
    path = tmp_path / "cache.duckdb"
    store = store_at(path)
    assert cache.load_structural_cache(store, lambda fn: fn(SourceConnection([]))) == 0
    assert store.publish(force=True)
    assert cache.lookup_structural_ids(path) == []
    store.close()


def test_stream_failure_rolls_back_partial_load_and_closes_cursor(tmp_path):
    store = store_at(tmp_path / "cache.duckdb")
    source = SourceConnection([("1", datetime(2026, 1, 1), "A", "X", "Chat")])
    original_fetch = source.stream.fetchmany
    count = 0
    def fetch(size):
        nonlocal count
        count += 1
        if count > 1:
            raise RuntimeError("source stream disconnected")
        return original_fetch(size)
    source.stream.fetchmany = fetch
    with pytest.raises(cache.AppealsStructuralCacheError, match="stream disconnected"):
        cache.load_structural_cache(store, lambda fn: fn(source), batch_size=1)
    assert source.stream.closed and source.autocommit is True
    source.rollback.assert_called_once()
    assert cache.STRUCTURAL_TABLE not in store._tables
    store.close()


def test_hydration_defensive_date_guard():
    base = pd.DataFrame({"req_reg_date": [datetime(2026, 2, 1)]})
    with pytest.raises(RuntimeError, match="date filter"):
        gp.validate_hydrated_filters(base, date_range=(None, "2026-01-31"))


def test_disabled_or_testing_runtime_skips_startup(monkeypatch):
    monkeypatch.setattr(cache, "load_structural_cache", Mock(side_effect=AssertionError("load invoked")))
    monkeypatch.setattr("workspace.utils.skill_runtime_mode.is_testing_runtime", lambda: False)
    cache.prepare_gateway_structural_cache(types.SimpleNamespace(settings={"gateway": {"appeals_analyzer": {"enable": False}}}))
    monkeypatch.setattr("workspace.utils.skill_runtime_mode.is_testing_runtime", lambda: True)
    cache.prepare_gateway_structural_cache(types.SimpleNamespace(settings={}))


def test_gateway_startup_publication_and_readiness(tmp_path, monkeypatch):
    import utils.db as db

    path = tmp_path / "cache.duckdb"
    store = store_at(path)
    monkeypatch.setattr(db, "run", lambda fn: fn(SourceConnection([])))
    monkeypatch.setattr(db, "shutdown", Mock())
    monkeypatch.setattr("workspace.utils.skill_runtime_mode.is_testing_runtime", lambda: False)
    ctx = types.SimpleNamespace(settings={}, cache_store=store, sync_service=Mock(), runtime_readiness=RuntimeReadiness())
    cache.prepare_gateway_structural_cache(ctx)
    assert ctx.runtime_readiness.check().status == "READY"
    assert path.exists()
    path.unlink()
    assert ctx.runtime_readiness.check().status == "NOT_READY"
    store.close()


@pytest.mark.parametrize("failure", ["load", "publish", "schema", "no_cache", "full_sync"])
def test_gateway_startup_failure_blocks_requests(tmp_path, monkeypatch, failure):
    import utils.db as db

    store = store_at(tmp_path / "cache.duckdb")
    monkeypatch.setattr("workspace.utils.skill_runtime_mode.is_testing_runtime", lambda: False)
    monkeypatch.setattr(db, "shutdown", Mock())
    monkeypatch.setattr(db, "run", Mock(side_effect=RuntimeError("GP unavailable")))
    ctx = types.SimpleNamespace(settings={}, cache_store=store, sync_service=Mock())
    if failure == "publish":
        monkeypatch.setattr(db, "run", lambda fn: fn(SourceConnection([])))
        monkeypatch.setattr(store, "publish", lambda **kwargs: False)
    elif failure == "schema":
        monkeypatch.setattr(db, "run", lambda fn: fn(SourceConnection([])))
        monkeypatch.setattr(cache, "open_structural_snapshot", Mock(side_effect=cache.AppealsStructuralCacheError("wrong published schema")))
    elif failure == "no_cache":
        ctx.cache_store = None
    elif failure == "full_sync":
        ctx.settings = {"skills": {"appeals_analyzer": {"enabled": True}}}
    with pytest.raises(cache.AppealsStructuralCacheError):
        cache.prepare_gateway_structural_cache(ctx)
    store.close()


def test_gateway_loader_precedes_start_and_channels():
    source = (ROOT / "gateway.py").read_text(encoding="utf-8")
    assert source.index("prepare_gateway_structural_cache(ctx)") < source.index("ctx.start()\n")
    assert source.index("prepare_gateway_structural_cache(ctx)") < source.index("GatewayRunner().run_forever")


@pytest.mark.parametrize("fails", [False, True])
def test_gateway_entrypoint_loads_before_request_loop(monkeypatch, fails):
    import gateway
    import config

    calls = []
    context = types.SimpleNamespace(
        settings={}, sync_service=None, cache_store=None,
        start=lambda: calls.append("start"), stop=lambda: calls.append("stop"),
    )
    app = types.ModuleType("lib.core.application_context")
    app.ApplicationContext = types.SimpleNamespace(create=lambda **kwargs: context)
    commands = types.ModuleType("nanobot.cli.commands")
    commands.__logo__ = "test"
    commands.__version__ = "0"
    runner = types.ModuleType("lib.lifecycle.gateway_runner")
    class Runner:
        def run_forever(self, run):
            calls.append("request_loop")
    runner.GatewayRunner = Runner
    for name, module in ((app.__name__, app), (commands.__name__, commands), (runner.__name__, runner)):
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(config, "_initialize_settings", lambda **kwargs: None)
    monkeypatch.setattr(gateway, "_configure_logging", lambda *args: None)
    monkeypatch.setattr(gateway, "_gateway_print_llm_calls", lambda: False)
    monkeypatch.setattr(gateway, "_project_version", lambda: "0")
    monkeypatch.setattr(gateway, "_report_db_pool_startup", lambda: None)
    def prepare(ctx):
        assert ctx is context
        calls.append("load_and_publish")
        if fails:
            raise cache.AppealsStructuralCacheError("startup failed")
    monkeypatch.setattr(cache, "prepare_gateway_structural_cache", prepare)
    args = types.SimpleNamespace(profile="test", smoke=False)
    if fails:
        with pytest.raises(gateway.ConfigurationError, match="startup failed"):
            gateway._entrypoint_main(args, ROOT, ROOT / "workspace")
        assert calls == ["load_and_publish"]
    else:
        gateway._entrypoint_main(args, ROOT, ROOT / "workspace")
        assert calls == ["load_and_publish", "start", "request_loop", "stop"]


def test_report_forwards_filters_and_keeps_them_for_followup(monkeypatch):
    observed = []
    monkeypatch.setattr(reports, "retrieve_via_srb_d3", lambda *args: ["42"])
    def hydrate(ids, date_range=None, **filters):
        observed.append((ids, date_range, filters))
        return pd.DataFrame({"id": ["42"], "prd": ["A"], "s_prd": ["X"]})
    monkeypatch.setattr(reports, "fetch_appeals_by_ids", hydrate)
    monkeypatch.setattr(reports, "rerank_via_srb_d3", lambda _, __, frame: frame.assign(score=1.0))
    filters = {"products": ["A"], "subproducts": ["X"]}
    asyncio.run(reports._search_population("session", "q", ["42"], ("2026-01-01", None), structural_filters=filters))
    assert observed == [(["42"], ("2026-01-01", None), filters)]
    monkeypatch.setattr(reports, "answer_complaint_details", lambda *args, **kwargs: "details")
    asyncio.run(reports._run_follow_up("session", "РѕР±СЂР°С‰РµРЅРёРµ 42", {
        "final_ids": ["42"], "structural_filters": filters, "date_range": ("2026-01-01", None),
    }))
    assert observed[-1] == observed[0]
