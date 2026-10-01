import pytest

from lib.services.sql_assistant_runtime import SqlAssistantRuntime, _indexed_ids_matching
from lib.services.kb_store import KbStoreError
from lib.services.hybrid_search import HybridIndex, prepare_from_frame


TABLE={"id":1,"table_name":"db.orders","group_key":"orders","layer":"dm","description":"orders","columns_summary":"id and amount","row_count":10,"dialect":"spark","updated_at":"now"}
COLUMNS=[{"id":11,"table_id":1,"column_name":"id","data_type":"BIGINT","description":"key","ordinal":1,"updated_at":"now"},{"id":12,"table_id":1,"column_name":"amount","data_type":"DOUBLE","description":"amount","ordinal":2,"updated_at":"now"}]
EXAMPLE={"id":101,"script_id":338,"km_id":"K","file_name":"orders.sql","file_path":"/orders.sql","nl":"sum orders","nl_variants":"[]","sql":"SELECT sum(amount) FROM db.orders","script_description":"sum amount","tables":"[\"db.orders\"]","dialect":"spark","updated_at":"now"}


class Provider:
    def execute_readonly(self, sql, params=None, max_rows=1000):
        source=COLUMNS if "kb_columns" in sql else [EXAMPLE] if "kb_examples" in sql else [TABLE]
        if "COUNT(*) AS row_count" in sql:
            columns=["row_count","max_updated_at"]
            values=[len(source),"now"]
            if "tables_max_updated_at" in sql: columns.append("tables_max_updated_at"); values.append("now")
            return {"columns":columns,"rows":[tuple(values)]}
        params=[str(v) for v in (params or [])]
        if "WHERE" in sql and params:
            if "CAST(c.table_id" in sql: source=[r for r in source if str(r.get("table_id")) in params]
            elif "CAST(c.id" in sql or "CAST(id" in sql: source=[r for r in source if str(r.get("id")) in params]
        if "SELECT c.*" in sql:
            source=[{**row,"table_name":TABLE["table_name"] if row.get("table_id")==TABLE["id"] else None} for row in source]
        cols=list(source[0]) if source else []
        return {"columns":cols,"rows":[tuple(row.get(c) for c in cols) for row in source[:max_rows]]}


@pytest.mark.asyncio
async def test_fake_end_to_end_retrieval_generation_validation_facts(monkeypatch):
    calls=[]
    def fake_llm(messages, **kwargs): calls.append(messages); return "SELECT sum(amount) AS total FROM db.orders"
    monkeypatch.setattr("lib.services.llm_client.call_llm",fake_llm)
    monkeypatch.setattr("lib.services.sql_assistant_runtime._default_llm_config",lambda: {"model":"fake","api_base":"http://fake","api_key":"","max_tokens":100,"temperature":0})
    docs=prepare_from_frame([TABLE],text_fields=("description","columns_summary","table_name"),group_field="group_key")
    monkeypatch.setattr("lib.services.sql_assistant_runtime.load_index",lambda *_a,**_k:(HybridIndex(docs),{"build_id":"test","source_signature":"{\"max_updated_at\":\"now\",\"row_count\":1}"}))
    runtime=SqlAssistantRuntime(Provider(),index_root="unused",score_floor=0.1)
    search=runtime.search("orders amount",corpus="tables")
    result=await runtime.generate(question="total amount",dialect="spark",table_ids=[1],example_ids=[101,999])
    assert search["items"][0]["id"] == 1
    assert result["status"] == "ok" and result["validation"]["valid"]
    assert result["facts"]["tables"] == ["db.orders"]
    assert result["facts"]["example_notes"][0]["script_id"] == 338
    assert result["missing_example_ids"] == ["999"]
    assert len(calls) == 1


def test_repeated_search_never_materializes_whole_corpus(monkeypatch):
    provider=Provider(); calls=[]
    docs=prepare_from_frame([TABLE],text_fields=("description","columns_summary","table_name"),group_field="group_key")
    def loader(*_args,**kwargs):
        calls.append(kwargs)
        return HybridIndex(docs),{"build_id":"stable","source_signature":"{\"max_updated_at\":\"now\",\"row_count\":1}"}
    monkeypatch.setattr("lib.services.sql_assistant_runtime.load_index",loader)
    original=provider.execute_readonly
    sql_calls=[]
    def counted(sql,*args,**kwargs): sql_calls.append(sql); return original(sql,*args,**kwargs)
    provider.execute_readonly=counted
    runtime=SqlAssistantRuntime(provider,index_root="unused",score_floor=0.1)
    for _ in range(3):
        assert runtime.search("orders",corpus="tables")["items"]
    assert not any("SELECT * FROM sqlagent.kb_tables ORDER BY id" in sql for sql in sql_calls)
    assert sum("COUNT(*) AS row_count" in sql for sql in sql_calls) == 3
    assert sum("WHERE CAST(id AS VARCHAR) IN" in sql for sql in sql_calls) == 3


def test_columns_loader_receives_no_dense_or_reranker(monkeypatch):
    provider=Provider(); captured={}
    docs=prepare_from_frame([{**row,"table_name":"db.orders"} for row in COLUMNS],text_fields=("table_name","column_name","data_type","description"))
    def loader(*_args,**kwargs):
        captured.update(kwargs)
        return HybridIndex(docs),{"build_id":"columns","source_signature":"{\"max_updated_at\":\"now\",\"row_count\":2}"}
    monkeypatch.setattr("lib.services.sql_assistant_runtime.load_index",loader)
    bomb=lambda *_args,**_kwargs: (_ for _ in ()).throw(AssertionError("model called"))
    runtime=SqlAssistantRuntime(provider,index_root="unused",score_floor=0.1,embedder=bomb,reranker=bomb)
    assert runtime.search("amount",corpus="columns")["items"][0]["column_name"] == "amount"
    assert runtime.search("amount",corpus="columns")["items"][0]["table_name"] == "db.orders"
    assert captured["embedder"] is None and captured["reranker"] is None


def test_stale_index_omits_deleted_row(monkeypatch):
    docs=prepare_from_frame([TABLE],text_fields=("description","table_name"))
    monkeypatch.setattr("lib.services.sql_assistant_runtime.load_index",lambda *_a,**_k:(HybridIndex(docs),{"build_id":"old","source_signature":"old"}))
    provider=Provider(); original=provider.execute_readonly
    def without_row(sql,params=None,max_rows=1000):
        if "WHERE CAST(id AS VARCHAR) IN" in sql: return {"columns":[],"rows":[]}
        return original(sql,params,max_rows)
    provider.execute_readonly=without_row
    result=SqlAssistantRuntime(provider,index_root="unused",score_floor=0.1).search("orders",corpus="tables")
    assert result["index_stale"] is True and result["items"] == []
    assert result["dropped_missing_from_kb"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(("requested","missing"), [([999],["999"]),([1,999],["999"])])
async def test_missing_requested_table_ids_block_llm(monkeypatch,requested,missing):
    called=[]
    monkeypatch.setattr("lib.services.llm_client.call_llm",lambda *_a,**_k: called.append(True))
    result=await SqlAssistantRuntime(Provider()).generate(question="x",dialect="spark",table_ids=requested)
    assert result["status"] == "grounding_error" and result["error_type"] == "unknown_table_id"
    assert result["missing_table_ids"] == missing and called == []


def test_tables_and_columns_table_id_filters_use_different_fields():
    table_docs=prepare_from_frame([{"id":1,"text":"a"},{"id":2,"text":"b"}],text_fields=("text",),metadata_fields=("id",))
    column_docs=prepare_from_frame([{"id":11,"table_id":1,"text":"a"},{"id":12,"table_id":2,"text":"b"}],text_fields=("text",),metadata_fields=("id","table_id"))
    assert _indexed_ids_matching("tables",HybridIndex(table_docs),{"table_ids":{"2"}}) == ["2"]
    assert _indexed_ids_matching("columns",HybridIndex(column_docs),{"table_ids":{"2"}}) == ["12"]


def test_examples_group_filter_resolves_to_full_physical_name(monkeypatch):
    runtime=SqlAssistantRuntime(Provider(),index_root="unused")
    monkeypatch.setattr(runtime.store,"tables_by_group_keys",lambda _keys:[{"id":10,"group_key":"cards","table_name":"schema_a.cards"}])
    resolved=runtime._resolve_search_filters("examples",{"group_keys":["cards"]})
    docs=prepare_from_frame([{"id":1,"text":"a","tables":"[\"schema_a.cards\"]"},{"id":2,"text":"b","tables":"[\"schema_b.cards\"]"}],text_fields=("text",),metadata_fields=("id","tables"))
    assert _indexed_ids_matching("examples",HybridIndex(docs),resolved) == ["1"]


def test_examples_table_ids_resolve_through_table_group_keys():
    runtime=SqlAssistantRuntime(Provider(),index_root="unused")
    resolved=runtime._resolve_search_filters("examples",{"table_ids":[1]})
    docs=prepare_from_frame([{"id":1,"text":"a","tables":"[\"db.orders\"]"},{"id":2,"text":"b","tables":"[\"db.loans\"]"}],text_fields=("text",),metadata_fields=("id","tables"))
    assert _indexed_ids_matching("examples",HybridIndex(docs),resolved) == ["1"]


def test_unknown_example_group_is_empty_allowed_set(monkeypatch):
    runtime=SqlAssistantRuntime(Provider(),index_root="unused")
    monkeypatch.setattr(runtime.store,"tables_by_group_keys",lambda _keys:[])
    resolved=runtime._resolve_search_filters("examples",{"group_keys":["missing"]})
    docs=prepare_from_frame([{"id":1,"text":"a","tables":"[\"db.orders\"]"}],text_fields=("text",),metadata_fields=("id","tables"))
    assert _indexed_ids_matching("examples",HybridIndex(docs),resolved) == []


def test_unsupported_columns_filter_is_explicit():
    runtime=SqlAssistantRuntime(Provider(),index_root="unused")
    with pytest.raises(KbStoreError) as caught:
        runtime.search("x",corpus="columns",filters={"dialect":"spark"})
    assert caught.value.code == "unsupported_filter"


def test_rank_ids_intersects_filters_before_ranking(monkeypatch):
    rows=[{"id":i,"table_name":f"db.t{i}","group_key":f"g{i}","description":f"table {i}","dialect":"spark","updated_at":"now"} for i in range(1,5)]
    docs=prepare_from_frame(rows,text_fields=("description",),metadata_fields=("id","dialect"))
    seen=[]
    index=HybridIndex(docs,reranker=lambda _query,texts:(seen.append(list(texts)) or [1.0 for _ in texts]))
    monkeypatch.setattr("lib.services.sql_assistant_runtime.load_index",lambda *_a,**_k:(index,{"build_id":"rank","source_signature":"sig"}))
    class RankProvider:
        def execute_readonly(self,sql,params=None,max_rows=1000):
            if "COUNT(*) AS row_count" in sql: return {"columns":["row_count","max_updated_at"],"rows":[(4,"now")]}
            selected=[row for row in rows if str(row["id"]) in {str(v) for v in (params or [])}]
            cols=list(rows[0]); return {"columns":cols,"rows":[tuple(row[c] for c in cols) for row in selected]}
    runtime=SqlAssistantRuntime(RankProvider(),index_root="unused",score_floor=0.1)
    result=runtime.search("table",corpus="tables",mode="rank_ids",ids=[1,2,3,4],filters={"table_ids":[4]},top_k=1)
    assert [item["id"] for item in result["items"]] == [4]
    assert seen == [["table 4"]]
