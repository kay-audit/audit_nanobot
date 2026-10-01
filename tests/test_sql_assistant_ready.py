import pytest
from lib.services.sql_assistant_runtime import NOT_FOUND, SqlAssistantRuntime, markdown_fence
from lib.services.hybrid_search import HybridIndex, prepare_from_frame


class Provider:
    def __init__(self, rows): self.rows=rows; self.calls=[]
    def execute_readonly(self, sql, params=None, max_rows=1000):
        self.calls.append((sql,list(params or [])))
        if "COUNT(*) AS row_count" in sql:
            latest=max((str(r.get("updated_at") or "") for r in self.rows),default="")
            return {"columns":["row_count","max_updated_at"],"rows":[(len(self.rows),latest)]}
        value=str((params or [""])[0]).strip("%")
        rows=self.rows
        if "WHERE CAST(script_id" in sql: rows=[r for r in rows if str(r.get("script_id"))==value]
        elif "WHERE lower(CAST(km_id" in sql: rows=[r for r in rows if str(r.get("km_id","")).lower()==value.lower()]
        elif "WHERE lower(CAST(file_name" in sql: rows=[r for r in rows if str(r.get("file_name","")).lower()==value.lower()]
        elif "WHERE lower(file_path" in sql: rows=[r for r in rows if value.lower() in str(r.get("file_path","")).lower()]
        cols=list(rows[0]) if rows else list(self.rows[0]) if self.rows else []
        return {"columns":cols,"rows":[tuple(r.get(c) for c in cols) for r in rows]}


def row(i,sql="SELECT 1;\r\n",km="99-1"):
    return {"id":i,"script_id":i,"km_id":km,"file_name":f"script_{i}.sql","file_path":f"/sql/script_{i}.sql","nl":"","nl_variants":"[]","sql":sql,"script_description":"desc","tables":"[]","dialect":"spark","updated_at":"2026-01-01"}


@pytest.mark.parametrize("prompt",["script_id 338","SCRIPT-ID: 338","покажи script_id=338"])
def test_exact_script_id_preserves_sql(prompt):
    body="-- Юникод ё\r\nSELECT `x`, '```' FROM t;\r\n"
    result=SqlAssistantRuntime(Provider([row(338,body)])).ready(prompt)
    assert result["status"]=="found" and body in result["content"] and result["verbatim"] is True


def test_exact_filename_path_km_and_duplicates():
    runtime=SqlAssistantRuntime(Provider([row(1,km="K-1"),row(2,km="K-1")]))
    assert runtime.ready("script_1.sql")["count"]==1
    assert runtime.ready("/sql/script_2.sql")["script_ids"]==[2]
    assert runtime.ready("km_id K-1")["count"]==2


def test_missing_exact_never_reads_catalog_or_generates():
    provider=Provider([row(1)]); result=SqlAssistantRuntime(provider).ready("script_id 999")
    assert result == {"status":"not_found","message":NOT_FOUND,"lookup":{"kind":"script_id","value":"999"}}
    assert len(provider.calls)==1 and "WHERE" in provider.calls[0][0]


def test_markdown_fence_handles_embedded_backticks_and_large_body():
    body="SELECT '```';\n" + ("-- большой текст ё\n"*20000)
    rendered=markdown_fence(body)
    assert body in rendered and rendered.startswith("````sql\n")


def test_semantic_ready_search_then_exact_lookup_preserves_source_and_blocks_fake_id(monkeypatch):
    body="SELECT * FROM real_source;\r\n"
    source=[row(338,body)]
    docs=prepare_from_frame(source,text_fields=("script_description","file_name"))
    signature='{\"max_updated_at\":\"2026-01-01\",\"row_count\":1}'
    monkeypatch.setattr("lib.services.sql_assistant_runtime.load_index",lambda *_a,**_k:(HybridIndex(docs),{"build_id":"test","source_signature":signature}))
    runtime=SqlAssistantRuntime(Provider(source),index_root="unused",score_floor=0.1)
    search=runtime.search("desc",corpus="examples")
    assert search["items"][0]["script_id"] == 338
    delivered=runtime.ready("script_id 338")
    assert body in delivered["content"]
    assert runtime.ready("script_id 777777")["status"] == "not_found"


def test_existing_non_read_only_source_is_still_delivered_unchanged():
    body="DELETE FROM legacy_table;\r\n"
    result=SqlAssistantRuntime(Provider([row(9,body)])).ready("script_id 9")
    assert result["status"] == "found" and body in result["content"]


def test_skill_forbids_auto_delivery_of_low_confidence_candidate():
    from pathlib import Path
    instructions=Path("workspace/skills/sql_assistant/SKILL.md").read_text(encoding="utf-8")
    assert "low_confidence=true" in instructions
    assert "не вызывай `sql_analyzer`" in instructions
