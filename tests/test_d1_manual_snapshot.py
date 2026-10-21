from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from lib.services.manual_snapshots import META, import_snapshot, metadata, quoted
from lib.services.subscription_resolver import SubscriptionResolver
from tools.refresh_d1_subscriptions import (
    GROUP,
    RESOURCES,
    fingerprint,
    new_store,
    offline_resources,
    refresh,
    rows_digest,
)

ACTIVE, CATALOG = list(RESOURCES)


def resources(users=None, options=None):
    users = users if users is not None else [{"ipa_login": "ipa", "sudir_login": "1", "schema_name": "DB", "sub_alias": "16_DEA_UNIT_CHANNEL"}]
    options = options if options is not None else [{"meta_code": "DB", "sub_alias": "available", "sub_name": "Реальная подписка"}]
    return {ACTIVE: (RESOURCES[ACTIVE], [users]), CATALOG: (RESOURCES[CATALOG], [options])}


@pytest.fixture
def published(tmp_path):
    path = tmp_path / "cache.duckdb"
    store = new_store(path)
    store._conn.execute("CREATE SCHEMA kb")
    store._conn.execute("CREATE TABLE kb.examples AS SELECT 7 id, 'Keep MixedCase' body")
    store._tables = ["kb.examples"]
    store._dirty = True
    assert store.publish(force=True)
    store.close()
    refresh(path, resources())
    return path


@pytest.mark.asyncio
async def test_subscribed_missing_and_digit_alias_from_published_snapshot(published):
    store = new_store(published)
    try:
        for login, status in [("1", "subscribed"), ("2", "not_subscribed")]:
            result = await SubscriptionResolver(store, identity_confirmed=True, identity=lambda login=login: login).resolve(["DB.TABLE"])
            assert result.sources[0]["status"] == status
            assert datetime.fromisoformat(result.snapshot["refreshed_at"]).tzinfo is not None
            if status == "subscribed":
                assert result.public_to_physical == {"prx_16_dea_unit_channel_db.table": "db.table"}
            else:
                assert result.sources[0]["options"][0]["sub_name"] == "Реальная подписка"
                assert "последнего обновления" in result.sources[0]["message"]
                assert not result.public_to_physical
    finally:
        store.close()


@pytest.mark.asyncio
async def test_manual_add_delete_preserves_old_snapshot_until_reload(published):
    old = new_store(published)
    try:
        resolver = SubscriptionResolver(old, identity_confirmed=True, identity=lambda: "2")
        before = await resolver.resolve(["db.t"])
        assert not before.allowed
        refresh(published, resources(users=[{"ipa_login": "new", "sudir_login": "2", "schema_name": "DB", "sub_alias": "new"}]))
        assert not (await resolver.resolve(["db.t"])).allowed
        current = new_store(published)
        try:
            result = await SubscriptionResolver(current, identity_confirmed=True, identity=lambda: "2").resolve(["db.t"])
            assert result.allowed and result.snapshot["generation"] != before.snapshot["generation"]
            assert not (await SubscriptionResolver(current, identity_confirmed=True, identity=lambda: "1").resolve(["db.t"])).allowed
            import_snapshot(current._conn, published)
            assert current.query_sql("SELECT * FROM kb.examples")["rows"][0]["id"] == 7
        finally:
            current.close()
    finally:
        old.close()


def test_failure_mid_second_resource_and_publish_failure_retain_file(published, monkeypatch):
    original = fingerprint(published)
    def broken():
        yield [{"meta_code": "DB", "sub_alias": "partial", "sub_name": "partial"}]
        raise OSError("source disconnected")
    partial = resources()
    partial[CATALOG] = (RESOURCES[CATALOG], broken())
    with pytest.raises(OSError):
        refresh(published, partial)
    assert fingerprint(published) == original
    store = new_store(published)
    store_type = type(store)
    store.close()
    monkeypatch.setattr(store_type, "publish", lambda *args, **kwargs: False)
    with pytest.raises(RuntimeError, match="publication"):
        refresh(published, resources(users=[]))
    assert fingerprint(published) == original


def test_complete_empty_snapshot_is_distinct_from_loader_failure(published):
    result = refresh(published, resources(users=[]))
    assert result["resources"][ACTIVE]["row_count"] == 0
    assert datetime.fromisoformat(result["refreshed_at"]) <= datetime.now(UTC)
    store = new_store(published)
    try:
        assert metadata(store._conn, GROUP)["resources"][ACTIVE]["row_count"] == 0
    finally:
        store.close()


@pytest.mark.asyncio
async def test_no_max_rows_clipping_and_two_users_are_isolated(tmp_path):
    path = tmp_path / "cache.duckdb"
    users = [{"ipa_login": "i", "sudir_login": "1", "schema_name": "DB", "sub_alias": f"alias_{i:04}"} for i in range(2501)]
    users.append({"ipa_login": "other", "sudir_login": "2", "schema_name": "DB", "sub_alias": "foreign"})
    refresh(path, resources(users=users))
    store = new_store(path)
    try:
        store.execute_readonly = Mock(side_effect=AssertionError("Public/truncating SQL API must not be used"))
        result = await SubscriptionResolver(store, identity_confirmed=True, identity=lambda: "1").resolve(["db.t"])
        assert result.allowed and len(result.sources[0]["active_aliases"]) == 2501
        assert "foreign" not in str(result.report())
        assert list(result.public_to_physical) == ["prx_alias_0000_db.t"]
    finally:
        store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["missing", "row_count", "metadata", "columns"])
async def test_bad_cache_is_not_absent_subscription(published, corruption):
    store = new_store(published)
    try:
        if corruption == "missing":
            store._conn.execute(f"DROP TABLE {quoted(ACTIVE)}")
        elif corruption == "row_count":
            store._conn.execute(f"DELETE FROM {quoted(ACTIVE)}")
        elif corruption == "columns":
            store._conn.execute(f"ALTER TABLE {quoted(ACTIVE)} DROP COLUMN sub_alias")
        else:
            store._conn.execute(f"UPDATE {quoted(META)} SET resources='bad json'")
        result = await SubscriptionResolver(store, identity_confirmed=True, identity=lambda: "1").resolve(["db.t"])
        assert not result.allowed and result.sources[0]["status"] == "subscription_cache_unavailable"
        assert not result.public_to_physical
    finally:
        store.close()


def test_private_tables_denied_to_generic_sql_and_schema(published):
    store = new_store(published)
    try:
        import_snapshot(store._conn, published)
        for sql in [f"SELECT * FROM {ACTIVE}", f"WITH a AS (SELECT * FROM {ACTIVE}) SELECT * FROM a", "SELECT * FROM query_table(?)"]:
            assert "error" in store.execute_readonly(sql, [ACTIVE] if "?" in sql else None)
            assert store.query_sql(sql, [ACTIVE] if "?" in sql else None)["status"] == "error"
            assert not store.explain(sql)["valid"]
        assert not store.get_schema(ACTIVE.split(".")[0])["tables"]
        assert store.query_sql("SELECT * FROM kb.examples")["status"] == "success"
    finally:
        store.close()


def test_offline_contract_checksum_counts_and_both_tables(tmp_path, published):
    package = {"format_version": 1, "complete": True, "tables": {}}
    for name, (columns, batches) in resources().items():
        rows = list(batches)[0]
        package["tables"][name] = {"columns": columns, "rows": rows, "row_count": len(rows), "sha256": rows_digest(rows)}
    path = tmp_path / "input.json"
    path.write_text(json.dumps(package), encoding="utf-8")
    refresh(published, offline_resources(path))
    before = fingerprint(published)
    for mutate in (lambda p: p.update(complete=False), lambda p: p["tables"].pop(CATALOG), lambda p: p["tables"][ACTIVE].update(row_count=100), lambda p: p["tables"][ACTIVE].update(sha256="wrong")):
        altered = json.loads(json.dumps(package))
        mutate(altered)
        path.write_text(json.dumps(altered), encoding="utf-8")
        with pytest.raises(ValueError):
            refresh(published, offline_resources(path))
        assert fingerprint(published) == before


def test_manual_resources_do_not_enter_polling(monkeypatch):
    from config import load_config_json
    from lib.core.skill_registration import build_resources_for_skill
    from lib.services.pg_duckdb_sync_service import PgDuckDbSyncService
    from lib.services.table_registry import table_registry
    cfg = load_config_json(Path(__file__).resolve().parents[1] / "project.json")["skills"]["sql_assistant"]
    items = build_resources_for_skill(cfg)
    manual = [item for item in items if item.sync_mode == "manual_snapshot"]
    assert {item.name for item in manual} == set(RESOURCES)
    assert all(item.tracking_column is None and item.label for item in manual)
    monkeypatch.setattr(table_registry, "table_resources", lambda: tuple(items))
    sync = PgDuckDbSyncService(dsn="", schema="main", tables=[item.name for item in items])
    assert not set(RESOURCES).intersection(sync._tables)
    assert len(sync._tables) == 3


@pytest.mark.asyncio
async def test_runtime_never_calls_gp_and_ready_stays_verbatim(published, monkeypatch):
    import workspace.utils.db as db
    from lib.services.sql_assistant_runtime import SqlAssistantRuntime
    monkeypatch.setattr(db, "fetch", Mock(side_effect=AssertionError("GP call in D1 runtime")))
    store = new_store(published)
    try:
        service = SubscriptionResolver(store, identity_confirmed=True, identity=lambda: "1")
        runtime = SqlAssistantRuntime(store, subscription_resolver=service)
        runtime.store.tables_by_ids = lambda ids: [{"id": 1, "table_name": "db.t"}]
        runtime.store.schema_for_tables = lambda ids: {"db.t": {"id": "BIGINT"}}
        for _ in range(2):
            report = await runtime.validate("SELECT id FROM db.t", dialect="spark", table_ids=[1])
            assert report["publishable"]
        monkeypatch.setattr("lib.services.llm_client.call_llm", lambda *args, **kwargs: "SELECT id FROM db.t")
        runtime.describe = AsyncMock(return_value={"tables": [{"id": 1, "table_name": "db.t"}], "examples": []})
        generated = await runtime.generate(question="Проверь чужой ID 2", dialect="spark", table_ids=[1], llm_config={"model": "fake"})
        assert generated["publishable"]
        source_sql = " SELECT 'MiXeD';  \r\n"
        runtime.store.example_lookup_exact = lambda *args: [{"script_id": 1, "sql": source_sql}]
        assert source_sql in runtime.ready("script_id 1")["content"]
        db.fetch.assert_not_called()
    finally:
        store.close()


def test_corrupt_private_manifest_fails_closed_for_public_sql(published):
    store = new_store(published)
    try:
        store._conn.execute(f"UPDATE {quoted(META)} SET resources='broken'")
        assert store.query_sql(f"SELECT * FROM {ACTIVE}")["status"] == "error"
    finally:
        store.close()


@pytest.mark.parametrize("bad_count", [False, True])
def test_operator_gp_stream_is_complete_repeatable_read_and_releases_connection(published, monkeypatch, bad_count):
    from types import SimpleNamespace

    from tools.refresh_d1_subscriptions import gp_resources
    from workspace.skills.sql_assistant.scripts import _pg_admin

    statements = []
    records = {name: list(batches)[0] for name, (_, batches) in resources().items()}

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, sql):
            statements.append(sql)
            self.name = next(name for name in records if quoted(name) in sql)
            self.done = False

        def fetchone(self):
            return (len(records[self.name]) + int(bad_count),)

        def fetchmany(self, size):
            if self.done:
                return []
            self.done = True
            return [tuple(row[c] for c in RESOURCES[self.name]) for row in records[self.name]]

    conn = Mock()
    conn.cursor.side_effect = lambda *args, **kwargs: Cursor()
    monkeypatch.setattr(_pg_admin, "connect", lambda args: conn)
    previous = fingerprint(published)
    if bad_count:
        with pytest.raises(ValueError, match="Incomplete GP"):
            with gp_resources(SimpleNamespace()) as items:
                refresh(published, items)
        assert fingerprint(published) == previous
    else:
        with gp_resources(SimpleNamespace()) as items:
            refresh(published, items)
        assert len(statements) == 4
        assert all("updated_at" not in sql for sql in statements)
    conn.set_session.assert_called_once_with(readonly=True, isolation_level="REPEATABLE READ")
    conn.rollback.assert_called_once()
    conn.close.assert_called_once()


def test_offline_cli_standalone_bootstrap_and_failure_retains_file(tmp_path):
    package = {"format_version": 1, "complete": True, "tables": {}}
    for name, (columns, batches) in resources().items():
        rows = list(batches)[0]
        package["tables"][name] = {"columns": columns, "rows": rows, "row_count": len(rows), "sha256": rows_digest(rows)}
    path = tmp_path / "complete.json"
    snapshot = tmp_path / "published.duckdb"
    path.write_text(json.dumps(package), encoding="utf-8")
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONUTF8"] = "1"
    command = [sys.executable, "-m", "tools.refresh_d1_subscriptions", "--snapshot", str(snapshot),
               "--offline-json", str(path), "--gateway-stopped"]
    result = subprocess.run(command, cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.splitlines()[-1])["status"] == "published"
    previous = fingerprint(snapshot)
    package["tables"][ACTIVE]["sha256"] = "invalid"
    path.write_text(json.dumps(package), encoding="utf-8")
    result = subprocess.run(command, cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 2
    assert json.loads(result.stdout.splitlines()[-1])["status"] == "refresh_failed"
    assert fingerprint(snapshot) == previous
