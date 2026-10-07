from __future__ import annotations

import importlib.util
import unittest
from unittest.mock import patch

from lib.services import sql_static
from workspace.skills.sql_assistant.scripts.bootstrap_kb import build_plan
from workspace.skills.sql_assistant.scripts.migrate_legacy_examples import extract_tables_json


class TestExternalDependencies(unittest.TestCase):
    def test_create_target_is_not_source(self):
        self.assertEqual(sql_static.physical_tables("CREATE TABLE work.tmp AS SELECT * FROM schema.real_table"), ["schema.real_table"])

    def test_intermediate_chain_reads_only_external_sources(self):
        sql = """
        CREATE TABLE work.tmp1 AS SELECT * FROM schema.real_table;
        CREATE TABLE work.tmp2 AS SELECT * FROM work.tmp1 JOIN schema.clients c ON 1=1;
        SELECT * FROM work.tmp2 JOIN work.tmp1 ON 1=1;
        """
        self.assertEqual(sql_static.physical_tables(sql), ["schema.real_table", "schema.clients"])

    def test_insert_overwrite_and_insert_into_targets_are_excluded(self):
        sql = """
        INSERT OVERWRITE TABLE work.tmp SELECT * FROM schema.real_table;
        INSERT INTO work.tmp2 SELECT * FROM work.tmp JOIN schema.clients ON 1=1;
        SELECT * FROM work.tmp2;
        """
        self.assertEqual(sql_static.physical_tables(sql), ["schema.real_table", "schema.clients"])

    def test_cte_sources_are_preserved_and_aliases_excluded(self):
        sql = "WITH x AS (SELECT * FROM schema.real_table), y AS (SELECT * FROM x) SELECT * FROM y JOIN schema.clients ON 1=1"
        self.assertEqual(sql_static.physical_tables(sql), ["schema.real_table", "schema.clients"])

    def test_cte_names_are_scoped_not_globally_hidden(self):
        sql = "SELECT * FROM x JOIN (WITH x AS (SELECT * FROM schema.real_table) SELECT * FROM x) q ON 1=1"
        self.assertEqual(set(sql_static.physical_tables(sql)), {"x", "schema.real_table"})

    def test_multiple_statements_drop_targets_and_deduplicate(self):
        sql = "DROP TABLE IF EXISTS schema.not_a_source; SELECT * FROM schema.real_table; SELECT * FROM schema.clients JOIN schema.real_table ON 1=1;"
        self.assertEqual(sql_static.physical_tables(sql), ["schema.real_table", "schema.clients"])

    def test_hive_storage_properties_do_not_block_ctas_sources(self):
        sql = """
        CREATE TABLE work.tmp STORED AS PARQUET
        TBLPROPERTIES ('note'='FROM fake.table; JOIN fake.other')
        AS SELECT * FROM schema.real_table JOIN schema.clients ON 1=1;
        SELECT * FROM work.tmp;
        """
        self.assertEqual(sql_static.physical_tables(sql), ["schema.real_table", "schema.clients"])

    def test_unsupported_ddl_fallback_only_reads_from_and_join(self):
        sql = "CREATE EXTERNAL TABLE work.tmp STORED AS PARQUET TBLPROPERTIES ('x'='CREATE TABLE fake.target FROM fake.source') AS SELECT * FROM schema.real_table JOIN schema.clients ON 1=1; SELECT * FROM work.tmp;"
        with patch.object(sql_static, "_dependency_ast_sources", return_value=None):
            self.assertEqual(sql_static.physical_tables(sql), ["schema.real_table", "schema.clients"])

    def test_create_without_query_marks_temporary_table_local(self):
        sql = "CREATE TEMPORARY TABLE tmp (id BIGINT) STORED AS PARQUET; INSERT OVERWRITE TABLE tmp SELECT * FROM schema.real_table; SELECT * FROM tmp;"
        self.assertEqual(sql_static.physical_tables(sql), ["schema.real_table"])

    def test_qualified_and_unqualified_local_references(self):
        self.assertEqual(sql_static.physical_tables("CREATE TABLE work.tmp AS SELECT * FROM schema.real_table; SELECT * FROM tmp;"), ["schema.real_table"])
        self.assertEqual(sql_static.physical_tables("CREATE TABLE tmp AS SELECT * FROM schema.real_table; SELECT * FROM work.tmp;"), ["schema.real_table"])

    def test_same_basename_in_other_schema_is_not_local(self):
        sql = "CREATE TABLE work.tmp AS SELECT * FROM schema.real_table; SELECT * FROM external.tmp;"
        self.assertEqual(sql_static.physical_tables(sql), ["schema.real_table", "external.tmp"])

    def test_use_schema_resolves_local_names_without_hiding_other_schema(self):
        sql = "USE work; CREATE TABLE tmp AS SELECT * FROM schema.real_table; SELECT * FROM work.tmp; SELECT * FROM external.tmp;"
        self.assertEqual(sql_static.physical_tables(sql), ["schema.real_table", "external.tmp"])

    def test_comments_strings_and_quoted_identifiers_are_safe(self):
        sql = "-- FROM fake.comment;\nSELECT 'FROM fake.literal; JOIN fake.other' FROM `Schema`.`Real_Table`; /* JOIN fake.block */ SELECT * FROM \"Schema\".\"Clients\";"
        self.assertEqual(sql_static.physical_tables(sql), ["Schema.Real_Table", "Schema.Clients"])

    def test_fallback_handles_subqueries_comma_sources_and_expression_from(self):
        sql = "SELECT EXTRACT(YEAR FROM created_at) FROM schema.real_table a, schema.clients b WHERE a.id IN (SELECT id FROM schema.ids)"
        with patch.object(sql_static, "_dependency_ast_sources", return_value=None):
            self.assertEqual(sql_static.physical_tables(sql), ["schema.real_table", "schema.clients", "schema.ids"])

    def test_sources_read_before_creation_are_not_removed_retroactively(self):
        sql = "SELECT * FROM work.tmp; CREATE TABLE work.tmp AS SELECT * FROM schema.real_table; SELECT * FROM work.tmp;"
        self.assertEqual(sql_static.physical_tables(sql), ["work.tmp", "schema.real_table"])

    def test_hive_from_first_insert(self):
        sql = "FROM schema.real_table INSERT OVERWRITE TABLE work.tmp SELECT id INSERT OVERWRITE TABLE work.tmp2 SELECT id; SELECT * FROM work.tmp2;"
        with patch.object(sql_static, "_dependency_ast_sources", return_value=None):
            self.assertEqual(sql_static.physical_tables(sql), ["schema.real_table"])

    def test_original_sql_is_unchanged_and_migration_uses_same_extractor(self):
        sql = "CREATE TABLE work.tmp AS SELECT * FROM schema.real_table;\r\nSELECT * FROM work.tmp;\r\n"
        original = sql
        self.assertEqual(extract_tables_json(sql, dialect="spark"), ('["schema.real_table"]', False))
        self.assertEqual(sql, original)

    def test_unterminated_literal_is_still_a_reported_parse_error(self):
        self.assertEqual(extract_tables_json("SELECT 'unterminated", dialect="spark"), (None, True))

    def test_bootstrap_dependencies_do_not_filter_metadata(self):
        sql = "CREATE TABLE t_team_sva_oarb.tmp STORED AS PARQUET TBLPROPERTIES ('x'='y') AS SELECT * FROM schema.real_table; CREATE TABLE t_team_sva_oarb.tmp2 AS SELECT * FROM t_team_sva_oarb.tmp; SELECT * FROM t_team_sva_oarb.tmp2;"
        example = {"script_id": 1, "script_body": sql}
        metadata = [
            {"schema_name": "schema", "table_name": "real_table", "field_name": "id"},
            {"schema_name": "schema", "table_name": "unreferenced", "field_name": "id"},
        ]
        plan = build_plan([example], metadata)
        self.assertEqual(plan.stats["unique_tables"], 2)
        self.assertEqual(plan.examples[0]["tables"], '["schema.real_table"]')
        self.assertEqual({row["table_name"] for row in plan.tables}, {"schema.real_table", "schema.unreferenced"})
        self.assertEqual(plan.examples[0]["sql"], sql)

    @unittest.skipUnless(importlib.util.find_spec("sqlglot"), "sqlglot is not installed in this environment")
    def test_sqlglot_scope_path_is_exercised(self):
        sources = sql_static._dependency_ast_sources("WITH x AS (SELECT * FROM schema.real_table) SELECT * FROM x JOIN schema.clients ON 1=1", "spark")
        self.assertIsNotNone(sources)
        self.assertEqual(sources, ["schema.real_table", "schema.clients"])


if __name__ == "__main__":
    unittest.main()
