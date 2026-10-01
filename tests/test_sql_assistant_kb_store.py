import pytest

from lib.services.kb_store import KbStore, KbStoreError
from lib.services.hybrid_search import prepare_from_frame
from workspace.skills.sql_assistant.scripts.build_index import FIELDS


class Provider:
    def __init__(self, result): self.result=result; self.calls=[]
    def execute_readonly(self, sql, params=None, max_rows=1000): self.calls.append((sql,params,max_rows)); return self.result


def test_rows_are_mapped_from_provider_columns():
    provider=Provider({"columns":["id","table_name"],"rows":[(1,"s.t")]})
    assert KbStore(provider).tables_by_ids([1]) == [{"id":1,"table_name":"s.t"}]
    assert provider.calls[0][1] == ["1"]


def test_exact_lookup_is_parameterized():
    provider=Provider({"columns":[],"rows":[]}); KbStore(provider).example_lookup_exact("file_path", "x%' OR 1=1 --")
    sql,params,_=provider.calls[0]
    assert "OR 1=1" not in sql and params == ["%x%' OR 1=1 --%"]


def test_missing_cache_becomes_controlled_error():
    with pytest.raises(KbStoreError) as caught: KbStore(Provider({"error":"DuckDbCacheStore is not ready"})).corpus_frame("tables")
    assert caught.value.code == "not_ready"


def test_source_signature_uses_only_cheap_aggregate():
    provider=Provider({"columns":["row_count","max_updated_at","tables_max_updated_at"],"rows":[(65000,"2026-09-15","2026-09-14")]})
    signature=KbStore(provider).source_signature("columns")
    sql,params,max_rows=provider.calls[0]
    assert "COUNT(*)" in sql and "MAX(c.updated_at)" in sql and "SELECT *" not in sql
    assert "sqlagent.kb_tables" in sql and "tables_max_updated_at" in signature
    assert params == [] and max_rows == 1 and '"row_count":65000' in signature


def test_columns_queries_join_table_name_without_redundant_column():
    provider=Provider({"columns":["id","table_id","column_name","table_name"],"rows":[(11,1,"amount","prd.orders")]})
    store=KbStore(provider)
    assert store.columns_by_ids([11])[0]["table_name"] == "prd.orders"
    sql,params,_=provider.calls[0]
    assert "LEFT JOIN sqlagent.kb_tables" in sql and "t.table_name AS table_name" in sql
    assert "CAST(c.id AS VARCHAR)" in sql and params == ["11"]


def test_columns_frame_uses_table_join_for_index_text():
    provider=Provider({"columns":["id","table_id","column_name","table_name"],"rows":[(11,1,"amount","prd.orders")]})
    row=KbStore(provider).corpus_frame("columns")[0]
    assert row["table_name"] == "prd.orders"
    assert "LEFT JOIN sqlagent.kb_tables" in provider.calls[0][0]
    fields,group,metadata=FIELDS["columns"]
    document=prepare_from_frame([row],text_fields=fields,group_field=group,metadata_fields=metadata)[0]
    assert document.text.startswith("prd.orders\namount")


def test_columns_signature_changes_with_tables_timestamp():
    first=KbStore(Provider({"columns":["row_count","max_updated_at","tables_max_updated_at"],"rows":[(2,"c1","t1")]})).source_signature("columns")
    second=KbStore(Provider({"columns":["row_count","max_updated_at","tables_max_updated_at"],"rows":[(2,"c1","t2")]})).source_signature("columns")
    assert first != second
