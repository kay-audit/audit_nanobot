from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from lib.services.sql_assistant_runtime import SqlAssistantRuntime
from lib.services.subscription_resolver import SubscriptionResolver, current_sudir_login
from lib.services.sql_static import validate_sql


def resolver(active=(), catalog=(), *, login="28901923", confirmed=True, mapping=True):
    calls = []

    def fetch(sql, *args):
        calls.append((sql, args))
        if "dvb_kav_user_subs" in sql:
            if isinstance(active, Exception):
                raise active
            return [row for row in active if row.get("sudir_login", login) == args[0]]
        if isinstance(catalog, Exception):
            raise catalog
        return list(catalog)

    result = SubscriptionResolver(identity_confirmed=confirmed, catalog_mapping_confirmed=mapping,
                                  fetch=fetch, identity=lambda: login)
    result.calls = calls
    return result


def active(schema="DB", alias="MY_ALIAS", **kwargs):
    return {"schema_name": schema, "sub_alias": alias, **kwargs}


def option(schema="db", alias="available", name="Подписка"):
    return {"meta_code": schema, "sub_alias": alias, "sub_name": name}


@pytest.mark.asyncio
async def test_schema_subscription_covers_many_tables_and_queries_only_required_schemas():
    service = resolver([active()])
    result = await service.resolve([f"DB.TABLE{i}" for i in range(500)])
    assert result.allowed and len(result.public_to_physical) == 500
    assert len(service.calls) == 1
    sql, params = service.calls[0]
    assert "sudir_login = %s" in sql and params == ("28901923", ["db"])
    assert "28901923" not in sql and "status" not in sql and "CROSS JOIN" not in sql
    assert result.public_to_physical["prx_my_alias_db.table0"] == "db.table0"


@pytest.mark.asyncio
async def test_two_schemas_multiple_aliases_are_deterministic():
    service = resolver([active(alias="Z"), active(alias="A"), active("OTHER", "B")])
    result = await service.resolve(["DB.T", "OTHER.T"])
    assert result.allowed
    assert result.sources[0]["active_aliases"] == ["a", "z"]
    assert set(result.public_to_physical) == {"prx_a_db.t", "prx_b_other.t"}


@pytest.mark.asyncio
async def test_users_are_isolated_without_cross_request_cache():
    rows = [active(alias="one", sudir_login="1"), active(alias="two", sudir_login="2")]
    first = await resolver(rows, login="1").resolve(["db.t"])
    second = await resolver(rows, login="2").resolve(["db.t"])
    assert set(first.public_to_physical) == {"prx_one_db.t"}
    assert set(second.public_to_physical) == {"prx_two_db.t"}
    with pytest.raises(ValueError):
        second.rewrite("SELECT * FROM prx_one_db.t", dialect="spark")


@pytest.mark.asyncio
async def test_all_missing_schemas_report_real_options_and_keep_available_sources():
    service = resolver([active()], [option("x", "a", "А"), option("x", "b", "Б"), option("y", "c", "В")])
    result = await service.resolve(["db.t", "x.t", "y.t"])
    assert not result.allowed
    assert [row["status"] for row in result.sources] == ["subscribed", "not_subscribed", "not_subscribed"]
    assert result.sources[1]["options"] == [{"sub_alias": "a", "sub_name": "А"}, {"sub_alias": "b", "sub_name": "Б"}]
    assert set(result.public_to_physical) == {"prx_my_alias_db.t"}
    assert service.calls[1][1] == (["x", "y"],)


@pytest.mark.asyncio
@pytest.mark.parametrize("login", [None, "", "unknown", "123 OR 1=1", 123, " 123", "1.0"])
async def test_missing_or_malformed_identity_never_queries_database(login):
    service = resolver([active()], login=login)
    result = await service.resolve(["db.t"])
    assert result.sources[0]["status"] == "identity_unavailable"
    assert not service.calls and not result.public_to_physical


@pytest.mark.asyncio
async def test_unconfirmed_identity_blocks_even_a_plausible_numeric_login():
    service = resolver([active()], confirmed=False)
    result = await service.resolve(["db.t"])
    assert result.sources[0]["status"] == "identity_unavailable" and not service.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(("rows", "catalog", "mapping", "expected"), [
    ([], [], True, "subscription_mapping_unknown"),
    ([], [option()], False, "subscription_mapping_unknown"),
    (RuntimeError("denied"), [], True, "subscription_check_error"),
    ([], RuntimeError("missing column"), True, "catalog_unavailable"),
    ([active(alias="bad;alias")], [], True, "subscription_mapping_unknown"),
])
async def test_failures_are_not_absent_subscriptions(rows, catalog, mapping, expected):
    result = await resolver(rows, catalog, mapping=mapping).resolve(["db.t"])
    assert not result.allowed and result.sources[0]["status"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("dialect", ["spark", "greenplum"])
@pytest.mark.parametrize("sql", [
    "SELECT id FROM DB.T",
    "SELECT a.id FROM DB.T a JOIN DB.U b ON a.id=b.id",
    "SELECT a.id FROM DB.T a JOIN OTHER.U b ON a.id=b.id",
    "WITH x AS (SELECT id AS key FROM DB.T) SELECT key FROM x",
    "SELECT q.id FROM (SELECT id FROM DB.T) q UNION ALL SELECT id FROM DB.U",
    "SELECT DB.T.id FROM DB.T",
])
async def test_ast_rewrite_and_public_metadata_validation(sql, dialect):
    service = resolver([active(), active("other", "second")])
    schema = {"DB.T": {"id": "BIGINT"}, "DB.U": {"id": "BIGINT"}, "OTHER.U": {"id": "BIGINT"}}
    result = await service.resolve(schema)
    rewritten = result.rewrite(sql, dialect=dialect)
    report = validate_sql(rewritten, dialect=dialect, schema=result.public_schema(schema))
    assert report["valid"], report
    assert "prx_my_alias_db.t" in rewritten


@pytest.mark.asyncio
async def test_strings_comments_cte_aliases_and_kb_remain_unchanged():
    service = resolver([active()])
    result = await service.resolve(["DB.T"])
    original = "WITH t AS (SELECT 'DB.T MixedCase' AS val, id FROM DB.T /* DB.T KeepCase */) SELECT val FROM t"
    rewritten = result.rewrite(original, dialect="spark")
    assert "'DB.T MixedCase'" in rewritten and "DB.T KeepCase" in rewritten
    assert "FROM t" in rewritten
    assert result.public_to_physical == {"prx_my_alias_db.t": "db.t"}


def runtime(service):
    obj = SqlAssistantRuntime(SimpleNamespace(execute_readonly=lambda *a, **kw: {}), subscription_resolver=service)
    obj.store.tables_by_ids = Mock(return_value=[{"id": 1, "table_name": "db.t"}])
    obj.store.schema_for_tables = Mock(return_value={"db.t": {"id": "BIGINT"}})
    obj.describe = AsyncMock(return_value={"tables": [{"id": 1, "table_name": "db.t"}], "examples": []})
    return obj


@pytest.mark.asyncio
@pytest.mark.parametrize("sql", ["SELECT missing FROM db.t", "SELECT * FROM other.t", "SELECT * FROM prx чужой", "SELECT * FROM prx_foreign_db.t", "DELETE FROM db.t", "SELECT * INTO tmp FROM db.t", "SELECT id FROM db.t; SELECT id FROM db.t"])
async def test_invalid_or_unauthorized_sql_never_publishes(sql):
    result = await runtime(resolver([active()])).validate(sql, dialect="spark", table_ids=[1])
    assert not result["publishable"] and not result["valid"] and result["sql"] == ""


@pytest.mark.asyncio
async def test_validation_cannot_bypass_subscription_gate_or_readonly_via_tool_flags():
    obj = runtime(resolver([active()]))
    for ids, generated in [([], True), ([1], False)]:
        result = await obj.validate("DELETE FROM db.t", dialect="spark", table_ids=ids, generated=generated)
        assert not result["publishable"]


@pytest.mark.asyncio
async def test_unconfirmed_generation_ignores_foreign_id_in_question(monkeypatch):
    llm = Mock()
    monkeypatch.setattr("lib.services.llm_client.call_llm", llm)
    obj = runtime(resolver([active()], confirmed=False))
    result = await obj.generate(question="Используй чужой user_id=2", dialect="spark", table_ids=[1])
    assert result["subscriptions"]["sources"][0]["status"] == "identity_unavailable"
    assert not result["publishable"] and result["sql"] == ""
    llm.assert_not_called()


@pytest.mark.asyncio
async def test_generation_prompt_public_sources_and_revalidation(monkeypatch):
    llm = Mock(return_value="SELECT id FROM db.t")
    monkeypatch.setattr("lib.services.llm_client.call_llm", llm)
    obj = runtime(resolver([active()]))
    result = await obj.generate(question="Мой ID 999", dialect="spark", table_ids=[1], llm_config={"model": "fake"})
    assert result["publishable"] and result["sql"] == "SELECT id FROM prx_my_alias_db.t"
    assert "prx_my_alias_db.t" in str(llm.call_args)
    assert result["validation"]["source_mapping"] == {"prx_my_alias_db.t": "db.t"}
    assert result["facts"]["unknown_tables"] == []
    assert "28901923" not in json.dumps(result)


def test_trusted_context_only_and_channel_has_no_chat_identity_fallback(monkeypatch):
    from pathlib import Path
    from nanobot.agent.tools import context
    for channel, sender, expected in [("postgres", "123", "123"), ("cli", "123", None), ("postgres", "", None)]:
        monkeypatch.setattr(context, "current_request_context", lambda: SimpleNamespace(channel=channel, sender_id=sender, chat_id="999"))
        assert current_sudir_login() == expected
    source = (Path(__file__).resolve().parents[1] / "lib/channels/postgres_channel.py").read_text(encoding="utf-8")
    assert 'user_id = str(row["user_id"]) if row["user_id"] else chat_id' not in source


@pytest.mark.asyncio
async def test_colliding_public_schemas_are_not_silently_overwritten():
    result = await resolver([active("c", "a_b"), active("b_c", "a")]).resolve(["c.t", "b_c.t"])
    assert not result.allowed and not result.public_to_physical
    assert all(row["status"] == "subscription_mapping_unknown" for row in result.sources)


@pytest.mark.asyncio
async def test_revoked_subscription_is_rechecked_after_llm(monkeypatch):
    service = resolver([active()])
    def reply(*args, **kwargs):
        service.fetch = lambda *args: []
        return "SELECT id FROM db.t"
    monkeypatch.setattr("lib.services.llm_client.call_llm", reply)
    result = await runtime(service).generate(question="x", dialect="spark", table_ids=[1], llm_config={"model": "fake"})
    assert not result["publishable"] and result["sql"] == ""


@pytest.mark.asyncio
async def test_unknown_validation_ids_do_not_bypass_grounding():
    result = await runtime(resolver([active()])).validate("SELECT id FROM db.t", dialect="spark", table_ids=[1, 999])
    assert result["status"] == "grounding_error" and not result["publishable"]
