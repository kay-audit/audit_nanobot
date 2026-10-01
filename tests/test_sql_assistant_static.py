import pytest

from lib.services.sql_dialects.sanitize import normalize_datetime_patterns
from lib.services.sql_static import sql_facts, validate_sql
from workspace.skills.sql_assistant.scripts.migrate_legacy_examples import extract_tables_json


SCHEMA={"db.orders":{"id":"BIGINT","created_at":"STRING","amount":"DOUBLE"},"db.clients":{"id":"BIGINT","name":"STRING"}}


def test_datetime_sanitizer_does_not_change_data_literal():
    sql="SELECT to_date(created_at, 'YYYY-MM-DD'), 'YYYY-MM-DD' FROM db.orders"
    assert normalize_datetime_patterns(sql) == "SELECT to_date(created_at, 'yyyy-MM-dd'), 'YYYY-MM-DD' FROM db.orders"


def test_multi_statement_and_destructive_are_rejected():
    assert any(i["code"]=="multiple_statements" for i in validate_sql("SELECT 1; DROP TABLE x",dialect="spark")["issues"])
    assert any(i["code"]=="not_read_only" for i in validate_sql("DELETE FROM db.orders",dialect="spark")["issues"])


def test_unknown_and_ambiguous_columns():
    unknown=validate_sql("SELECT missing FROM db.orders",dialect="spark",schema=SCHEMA)
    assert any(i["code"]=="unknown_column" for i in unknown["issues"])
    ambiguous=validate_sql("SELECT id FROM db.orders o JOIN db.clients c ON o.id=c.id",dialect="spark",schema=SCHEMA)
    assert any(i["code"]=="ambiguous_column" for i in ambiguous["issues"])


def test_unknown_table_is_warning_and_facts_cover_mechanics():
    result=validate_sql("SELECT x FROM db.unknown",dialect="spark",schema=SCHEMA,generated=False)
    assert any(w["code"]=="unknown_table" for w in result["warnings"])
    facts=sql_facts("SELECT o.id, count(*) AS n FROM db.orders o JOIN db.clients c ON o.id=c.id WHERE o.amount>10 GROUP BY o.id ORDER BY n DESC LIMIT 5",dialect="spark",schema=SCHEMA)
    assert set(facts["tables"]) == {"db.orders","db.clients"}
    assert facts["join_keys"] and facts["filters"]["where"]
    assert facts["aggregations"] and facts["group_by"] and facts["limit"] == "5"
    assert facts["literal_values_verified"] is False


def test_generated_unknown_physical_table_is_invalid():
    result=validate_sql("SELECT id FROM db.invented",dialect="spark",schema=SCHEMA,generated=True)
    assert result["valid"] is False and result["status"] == "invalid"
    assert any(issue["code"] == "unknown_table" for issue in result["issues"])


def test_cte_name_is_not_an_unknown_physical_table():
    result=validate_sql("WITH x AS (SELECT id FROM db.orders) SELECT id FROM x",dialect="spark",schema=SCHEMA,generated=True)
    assert not any(issue["code"] == "unknown_table" for issue in result["issues"])


def test_legacy_table_extraction_is_canonical_and_preserves_sql():
    sql="WITH recent AS (SELECT * FROM prd.orders o) SELECT * FROM recent r JOIN prd.clients c ON r.client_id=c.id JOIN prd.orders o2 ON o2.id=r.id;\r\n"
    original=sql
    payload,error=extract_tables_json(sql,dialect="spark")
    assert error is False and payload == '["prd.orders","prd.clients"]'
    assert sql == original


@pytest.mark.parametrize(("sql","expected"),[
    ("SELECT * FROM prd.orders",'["prd.orders"]'),
    ("SELECT * FROM prd.orders o JOIN prd.clients AS c ON o.client_id=c.id",'["prd.orders","prd.clients"]'),
])
def test_legacy_table_extraction_simple_and_join(sql,expected):
    assert extract_tables_json(sql,dialect="spark") == (expected,False)


def test_legacy_table_extraction_parse_failure_keeps_row_migratable():
    payload,error=extract_tables_json("SELECT 'unterminated",dialect="spark")
    assert payload is None and error is True
