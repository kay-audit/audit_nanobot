from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import unittest
from types import SimpleNamespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from lib.services.kb_store import KB_SCHEMA
from workspace.skills.sql_assistant.scripts import bootstrap_kb as module


def example(identity=338, sql="SELECT * FROM prd.ORDERS"):
    return {"script_id": identity, "km_id": "K", "file_path": "/orders.sql", "file_name": "orders.sql", "script_body": sql, "script_summary": "Orders"}


def metadata_row(schema="prd", table="ORDERS", field="order_id", **values):
    return {"schema_name": schema, "schema_descr": "Schema description", "table_name": table, "table_descr": "Orders description", "field_name": field, "field_descr": "Column description", "field_type": "BIGINT", **values}


def fixture_tables(sql, *, dialect):
    if sql == "BROKEN SQL":
        raise ValueError("parse error")
    return re.findall(r"FROM\s+([\w.]+)", sql, re.IGNORECASE)


class Cursor:
    def __init__(self, connection):
        self.connection = connection
        self.description = []
        self.rows = []
        self.rowcount = 0
        self.position = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        sql = sql.strip()
        self.connection.calls.append((sql, params))
        self.rowcount = 0
        if sql.startswith("SELECT"):
            if "70_aam_scripts_examples" in sql:
                rows = self.connection.examples
            elif self.connection.metadata_table.replace('"', '') in sql.replace('"', ''):
                rows = self.connection.metadata
            else:
                raise AssertionError(f"Unexpected SELECT: {sql}")
            fields = sql.split(" FROM ")[0].removeprefix("SELECT ").split(", ")
            self.description = [(field,) for field in fields]
            self.rows = [tuple(row.get(field) for field in fields) for row in rows]
            self.position = 0
            return
        self.connection.writes.append((sql, params))
        if sql.startswith("DROP TABLE"):
            self.connection.stage = []
            return
        if sql.startswith("CREATE TEMP TABLE"):
            return
        kind = next(kind for kind in ("examples", "tables", "columns") if f'"kb_{kind}"' in sql)
        stored = self.connection.kb[kind]
        if "tmp_sql_assistant_upsert" in sql:
            if sql.startswith("UPDATE"):
                for row in self.connection.stage:
                    if row["id"] not in stored:
                        continue
                    changes = dict(row)
                    if changes.get("tables") is None and "COALESCE" in sql:
                        changes.pop("tables", None)
                    stored[row["id"]].update(changes)
                    self.rowcount += 1
            elif sql.startswith("INSERT"):
                for row in self.connection.stage:
                    if row["id"] not in stored:
                        stored[row["id"]] = dict(row)
                        self.rowcount += 1
            return
        if sql.startswith("UPDATE"):
            identity = params[-1]
            if identity not in stored:
                return
            fields = re.findall(r'"([^\"]+)"=(?:COALESCE\(%s,"[^\"]+"\)|%s)', sql)
            for field, value in zip(fields, params):
                if field == "tables" and value is None and "COALESCE" in sql:
                    continue
                stored[identity][field] = value
            stored[identity]["updated_at"] = params[-2]
            self.rowcount = 1
        elif sql.startswith("INSERT"):
            fields = [field.strip().strip('"') for field in sql.split(" (", 1)[1].split(") VALUES", 1)[0].split(",")]
            row = dict(zip(fields, params))
            if row["id"] in stored:
                raise AssertionError("Duplicate primary key")
            stored[row["id"]] = row
            self.rowcount = 1
        else:
            raise AssertionError(f"Unexpected write: {sql}")

    def fetchmany(self, size):
        batch = self.rows[self.position:self.position + size]
        self.position += len(batch)
        return batch


class Connection:
    def __init__(self, examples=None, metadata=None, metadata_table=module.DEFAULT_METADATA_TABLE):
        self.examples = [example()] if examples is None else examples
        self.metadata = [metadata_row()] if metadata is None else metadata
        self.metadata_table = metadata_table
        self.calls, self.writes = [], []
        self.kb = {kind: {} for kind in ("examples", "tables", "columns")}
        self.readonly = False
        self.rollbacks = 0

    def cursor(self):
        return Cursor(self)

    def set_session(self, *, readonly):
        self.readonly = readonly

    def rollback(self):
        self.rollbacks += 1

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class TestBootstrap(unittest.TestCase):
    def setUp(self):
        extractor = patch.object(module, "physical_tables", side_effect=fixture_tables)
        extractor.start()
        self.addCleanup(extractor.stop)
        def execute_values(cursor, sql, values, **kwargs):
            fields = re.findall(r'"([^"]+)"', sql)
            cursor.connection.stage = [dict(zip(fields, row)) for row in values]
        driver = patch.dict(sys.modules, {"psycopg2.extras": SimpleNamespace(execute_values=execute_values)})
        driver.start()
        self.addCleanup(driver.stop)

    def test_two_fields_one_table_and_mapping(self):
        plan = module.build_plan([], [metadata_row(), metadata_row(field="amount", field_type="DECIMAL", field_descr="Amount")])
        self.assertEqual(len(plan.tables), 1)
        self.assertEqual(len(plan.columns), 2)
        table = plan.tables[0]
        self.assertEqual(table["table_name"], "prd.ORDERS")
        self.assertEqual(table["description"], "Orders description")
        self.assertEqual(table["dialect"], "spark")
        self.assertEqual(table["columns_summary"], "amount:DECIMAL, order_id:BIGINT")
        for row in plan.columns:
            self.assertEqual(row["table_id"], table["id"])
            self.assertIsNone(row["ordinal"])
        self.assertEqual(plan.columns[0]["description"], "Amount")

    def test_identical_basenames_in_two_schemas_have_distinct_ids(self):
        plan = module.build_plan([], [metadata_row(schema="a"), metadata_row(schema="b")])
        self.assertEqual(plan.stats["unique_tables"], 2)
        self.assertEqual(len({row["id"] for row in plan.tables}), 2)
        self.assertEqual(len({row["id"] for row in plan.columns}), 2)
        by_id = {row["id"]: row["table_name"] for row in plan.tables}
        self.assertEqual([by_id[row["table_id"]] for row in plan.columns], ["a.ORDERS", "b.ORDERS"])

    def test_ids_are_stable_lowercase_positive_bigints(self):
        first = module.build_plan([], [metadata_row()])
        second = module.build_plan([], [metadata_row(schema="PRD", table="orders", field="ORDER_ID")])
        for kind, key in (("tables", "prd.orders"), ("columns", "prd.orders.order_id")):
            identity = getattr(first, kind)[0]["id"]
            expected = int.from_bytes(hashlib.blake2b(key.encode(), digest_size=8).digest(), "big") & ((1 << 63) - 1)
            self.assertEqual(identity, expected or 1)
            self.assertEqual(identity, getattr(second, kind)[0]["id"])
            self.assertGreater(identity, 0)
            self.assertLess(identity, 2**63)

    def test_ids_stable_between_processes(self):
        command = "from workspace.skills.sql_assistant.scripts.bootstrap_kb import _synthetic_id; print(_synthetic_id('prd.orders'), _synthetic_id('prd.orders.order_id'))"
        results = [subprocess.check_output([sys.executable, "-B", "-c", command], cwd=Path(__file__).resolve().parent.parent, env={**os.environ, "PYTHONHASHSEED": seed}, text=True) for seed in ("1", "2")]
        self.assertEqual(*results)

    def test_null_and_empty_identity_fields_are_invalid(self):
        rows = [metadata_row()]
        for field in ("schema_name", "table_name", "field_name"):
            for value in (None, "", "  "):
                rows.append(metadata_row(**{field: value}))
        plan = module.build_plan([], rows)
        self.assertEqual(plan.stats["metadata_rows_read"], 10)
        self.assertEqual(plan.stats["metadata_invalid_rows"], 9)
        self.assertEqual(plan.stats["unique_tables"], 1)
        self.assertEqual(plan.stats["unique_columns"], 1)

    def test_duplicate_columns_are_counted_and_deduplicated_case_insensitively(self):
        rows = [metadata_row(), metadata_row(schema="PRD", table="orders", field="ORDER_ID"), metadata_row()]
        first = module.build_plan([], rows)
        second = module.build_plan([], reversed(rows))
        self.assertEqual(first.stats["duplicate_columns"], 2)
        self.assertEqual(first.stats["unique_columns"], 1)
        self.assertEqual(first.tables, second.tables)
        self.assertEqual(first.columns, second.columns)

    def test_conflicting_duplicate_fails_before_writes(self):
        connection = Connection(metadata=[metadata_row(), metadata_row(field_type="STRING")])
        with self.assertRaisesRegex(ValueError, "Conflicting duplicate"):
            module.bootstrap(connection)
        self.assertEqual(connection.writes, [])

    def test_conflicting_table_description_fails_before_writes(self):
        connection = Connection(metadata=[metadata_row(), metadata_row(field="other", table_descr="Other")])
        with self.assertRaisesRegex(ValueError, "Conflicting metadata table"):
            module.bootstrap(connection)
        self.assertEqual(connection.writes, [])

    def test_summary_is_bounded_and_order_independent(self):
        rows = [metadata_row(field=f"c{i:03}") for i in range(60)]
        first = module.build_plan([], rows, summary_columns=3)
        second = module.build_plan([], reversed(rows), summary_columns=3)
        self.assertEqual(first.tables, second.tables)
        self.assertEqual(first.columns, second.columns)
        self.assertEqual(first.tables[0]["columns_summary"], "c000:BIGINT, c001:BIGINT, c002:BIGINT")
        self.assertEqual(len(first.columns), 60)

    def test_nullable_descriptions_and_types(self):
        plan = module.build_plan([], [metadata_row(table_descr=None, field_descr=None, field_type=None)])
        self.assertIsNone(plan.tables[0]["description"])
        self.assertIsNone(plan.columns[0]["description"])
        self.assertIsNone(plan.columns[0]["data_type"])

    def test_examples_parse_errors_do_not_filter_metadata(self):
        sql = "-- comment\r\nSELECT * FROM external.unlisted;\r\n"
        plan = module.build_plan([example(sql=sql), example(339, "BROKEN SQL")], [metadata_row()])
        self.assertEqual(plan.examples[0]["sql"], sql)
        self.assertEqual(json.loads(plan.examples[0]["tables"]), ["external.unlisted"])
        self.assertEqual(plan.examples[1]["sql"], "BROKEN SQL")
        self.assertIsNone(plan.examples[1]["tables"])
        self.assertEqual(plan.stats["examples_parse_errors"], 1)
        self.assertEqual(plan.stats["examples_to_upsert"], 2)
        self.assertEqual(plan.stats["tables_to_upsert"], 1)
        self.assertEqual(plan.stats["columns_to_upsert"], 1)

    def test_empty_or_invalid_examples_still_load_metadata(self):
        for rows in ([], [example(sql="BROKEN SQL")], [example(sql=None)], [example(), example()]):
            with self.subTest(rows=rows):
                connection = Connection(examples=rows)
                stats = module.bootstrap(connection, dry_run=True)
                self.assertEqual(stats["unique_tables"], 1)
                self.assertEqual(stats["unique_columns"], 1)
                self.assertEqual(len(connection.calls), 2)

    def test_empty_metadata_does_not_remove_examples(self):
        plan = module.build_plan([example()], [])
        self.assertEqual(plan.stats["examples_to_upsert"], 1)
        self.assertEqual(plan.stats["unique_tables"], 0)
        self.assertEqual(plan.stats["unique_columns"], 0)

    def test_catalog_scale_counts_without_legacy_dependencies(self):
        table_count, column_count = 5049, 105910
        per_table, extra = divmod(column_count, table_count)
        rows = (
            metadata_row(table=f"t{table}", field=f"c{column}")
            for table in range(table_count)
            for column in range(per_table + (table < extra))
        )
        plan = module.build_plan([example(sql="BROKEN SQL")], rows)
        self.assertEqual(plan.stats["metadata_rows_read"], column_count)
        self.assertEqual(plan.stats["unique_tables"], table_count)
        self.assertEqual(plan.stats["tables_to_upsert"], table_count)
        self.assertEqual(plan.stats["unique_columns"], column_count)
        self.assertEqual(plan.stats["columns_to_upsert"], column_count)
        self.assertEqual(plan.stats["duplicate_columns"], 0)
        self.assertEqual(plan.stats["examples_parse_errors"], 1)

    def test_dry_run_reads_only_independent_sources(self):
        connection = Connection(metadata=[metadata_row(), metadata_row(table="UNREFERENCED")])
        stats = module.bootstrap(connection, dry_run=True, batch_size=1)
        self.assertEqual(connection.writes, [])
        self.assertEqual(len(connection.calls), 2)
        self.assertTrue(all(sql.startswith("SELECT") for sql, _ in connection.calls))
        self.assertIn('70_aam_scripts_examples', connection.calls[0][0])
        self.assertIn('dvb_kav_repl_test', connection.calls[1][0])
        self.assertNotIn("WHERE", connection.calls[1][0])
        self.assertNotIn("stock_element", connection.calls[1][0])
        required = {"examples_read", "examples_parse_errors", "examples_to_upsert", "metadata_rows_read", "metadata_invalid_rows", "unique_tables", "unique_columns", "tables_to_upsert", "columns_to_upsert", "duplicate_columns", "dry_run"}
        self.assertTrue(required <= stats.keys())
        self.assertEqual(stats["tables_to_upsert"], 2)
        self.assertEqual(stats["columns_to_upsert"], 2)
        self.assertNotIn("upsert", stats)

    def test_dry_run_cli_enforces_readonly_and_rolls_back(self):
        connection = Connection()
        with patch.object(module, "connect", return_value=connection), redirect_stdout(io.StringIO()):
            self.assertEqual(module.main(["--dry-run"]), 0)
        self.assertTrue(connection.readonly)
        self.assertEqual(connection.rollbacks, 1)
        self.assertEqual(connection.writes, [])

    def test_custom_metadata_table_is_used_once(self):
        connection = Connection(metadata_table='"custom"."catalog"')
        module.bootstrap(connection, metadata_table="custom.catalog", dry_run=True)
        self.assertIn('FROM "custom"."catalog"', connection.calls[1][0])

    def test_idempotent_upsert_preserves_enrichment_and_unknown_example_tables(self):
        connection = Connection()
        first = module.bootstrap(connection)
        self.assertTrue(all(counts == {"inserted": 1, "updated": 0} for counts in first["upsert"].values()))
        table = next(iter(connection.kb["tables"].values()))
        connection.kb["examples"][338]["nl"] = "Enriched"
        table["group_key"] = "Existing"
        connection.examples[0]["script_body"] = "BROKEN SQL"
        connection.metadata[0]["field_type"] = "STRING"
        second = module.bootstrap(connection)
        self.assertTrue(all(counts == {"inserted": 0, "updated": 1} for counts in second["upsert"].values()))
        self.assertEqual(connection.kb["examples"][338]["nl"], "Enriched")
        self.assertEqual(json.loads(connection.kb["examples"][338]["tables"]), ["prd.ORDERS"])
        self.assertEqual(table["group_key"], "Existing")
        self.assertEqual(next(iter(connection.kb["columns"].values()))["data_type"], "STRING")
        self.assertTrue(all("ON CONFLICT" not in sql for sql, _ in connection.writes))

    def test_table_hash_collision_fails_before_writes(self):
        connection = Connection(metadata=[metadata_row(schema="a"), metadata_row(schema="b")])
        with patch.object(module, "_synthetic_id", return_value=7):
            with self.assertRaisesRegex(module.SyntheticIdCollisionError, "collision"):
                module.bootstrap(connection)
        self.assertEqual(connection.writes, [])

    def test_column_hash_collision_fails_before_writes(self):
        connection = Connection(metadata=[metadata_row(), metadata_row(field="other")])
        original = module._synthetic_id
        with patch.object(module, "_synthetic_id", side_effect=lambda key: 7 if key.count(".") == 2 else original(key)):
            with self.assertRaisesRegex(module.SyntheticIdCollisionError, "collision"):
                module.bootstrap(connection)
        self.assertEqual(connection.writes, [])

    def test_ambiguous_dotted_logical_keys_raise_collision(self):
        with self.assertRaises(module.SyntheticIdCollisionError):
            module.build_plan([], [metadata_row(schema="a.b", table="c"), metadata_row(schema="a", table="b.c")])

    def test_unsafe_identifiers_rejected_before_reads(self):
        for kwargs in ({"metadata_table": "bad;DROP TABLE"}, {"target_kb_schema": "bad;DROP SCHEMA"}, {"source_table": "bad;DROP TABLE"}):
            connection = Connection()
            with self.assertRaises(ValueError):
                module.bootstrap(connection, **kwargs)
            self.assertEqual(connection.calls, [])

    def test_invalid_batch_parameters_rejected_before_reads(self):
        for kwargs in ({"batch_size": 0}, {"summary_columns": 0}):
            connection = Connection()
            with self.assertRaises(ValueError):
                module.bootstrap(connection, **kwargs)
            self.assertEqual(connection.calls, [])

    def test_defaults_and_metadata_flag(self):
        args = module.parser().parse_args(["--dry-run"])
        self.assertEqual(args.target_kb_schema, KB_SCHEMA)
        self.assertEqual(args.source_table, f"{KB_SCHEMA}.70_aam_scripts_examples")
        self.assertEqual(args.metadata_table, module.DEFAULT_METADATA_TABLE)
        self.assertIsNone(args.dsn_env)
        self.assertIsNone(args.profile)
        self.assertEqual(module.parser().parse_args(["--metadata-table", "a.b"]).metadata_table, "a.b")


if __name__ == "__main__":
    unittest.main()
