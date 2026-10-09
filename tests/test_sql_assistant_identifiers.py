from __future__ import annotations

import copy
import importlib.util
import sys
import unittest
from types import ModuleType
from unittest.mock import patch

from lib.services.sql_static import _normalize_schema, _qualification_schema, validate_sql
from lib.services.kb_store import KbStore


CASES = [("HRPL_LM_SELFSERVICE_SRC.PRODUCT", "INSERTED_DTTM"),
         ("UVZ_SELFSERVICE_SRC.MV_UVZ_WORK_PLANS", "PA_ID")]


class Identifier:
    def __init__(self, name):
        self.name = name
    def set(self, key, value):
        self.name = value


class Table:
    catalog = ""
    def __init__(self, name):
        self.db, self.name = name.split(".")
        self.alias_or_name = self.name


class Column:
    table = ""
    def __init__(self, name):
        self.name = name
    def sql(self):
        return self.name


class Select:
    key = "select"
    def __init__(self, table, column):
        self.table, self.column = Table(table.casefold()), Column(column.casefold())
        self.identifiers = [Identifier(part) for part in (*table.split("."), column)]
    def walk(self):
        return [self]
    def find_all(self, kind):
        return {Table: [self.table], Column: [self.column], Identifier: self.identifiers}.get(kind, [])
    def copy(self):
        return copy.deepcopy(self)
    def sql(self, **kwargs):
        return "SELECT " + self.column.name + " FROM " + self.table.db + "." + self.table.name


class TestIdentifiers(unittest.TestCase):
    def test_normalization_and_nested_schema(self):
        for table, column in CASES:
            normalized = _normalize_schema({table: {column: "STRING"}})
            schema, name = table.casefold().split(".")
            self.assertEqual(_qualification_schema(normalized), {schema: {name: {column.casefold(): "STRING"}}})

    def test_parser_lowercase_matches_uppercase_kb_without_real_dependencies(self):
        for table, column in CASES:
            with self.subTest(table=table):
                tree = Select(table, column)
                modules = {name: ModuleType(name) for name in
                           ("sqlglot", "sqlglot.exp", "sqlglot.optimizer", "sqlglot.optimizer.qualify", "sqlglot.schema")}
                exp = modules["sqlglot.exp"]
                exp.Select, exp.Table, exp.Column, exp.Identifier = Select, Table, Column, Identifier
                exp.Union, exp.Subquery, exp.CTE = type("Union", (), {}), type("Subquery", (), {}), type("CTE", (), {})
                modules["sqlglot"].exp = exp
                modules["sqlglot"].parse = lambda *args, **kwargs: [tree]
                class MappingSchema:
                    def __init__(self, mapping, **kwargs):
                        self.mapping = mapping
                modules["sqlglot.schema"].MappingSchema = MappingSchema
                def qualify(expression, *, schema, **kwargs):
                    owner, name = table.casefold().split(".")
                    self.assertIn(column.casefold(), schema.mapping[owner][name])
                    self.assertTrue(all(item.name == item.name.casefold() for item in expression.identifiers))
                modules["sqlglot.optimizer.qualify"].qualify = qualify
                with patch.dict(sys.modules, modules):
                    result = validate_sql(f"SELECT {column} FROM {table}", schema={table: {column: "STRING"}})
                self.assertTrue(result["valid"], result)
                self.assertNotIn("unknown_column", [issue["code"] for issue in result["issues"]])

    def test_exact_table_lookup_is_parameterized_and_not_semantic(self):
        class Provider:
            def execute_readonly(self, sql, params=None, max_rows=1000):
                self.sql, self.params = sql, params
                return {"columns": ["id", "table_name"], "rows": [(1, CASES[1][0])]}
        provider = Provider()
        rows = KbStore(provider).tables_by_names([CASES[1][0]])
        self.assertEqual(rows[0]["table_name"], CASES[1][0])
        self.assertIn("lower(table_name) IN (?)", provider.sql)
        self.assertNotIn(CASES[1][0], provider.sql)
        self.assertEqual(provider.params, [CASES[1][0].lower()])


@unittest.skipUnless(importlib.util.find_spec("sqlglot"), "real sqlglot is not installed")
class TestRealParserIdentifiers(unittest.TestCase):
    def test_spark_upper_lower_and_qualified_identifiers(self):
        import sqlglot
        from sqlglot import exp
        for table, column in CASES:
            for text in (column, column.lower(), "p." + column.lower()):
                with self.subTest(table=table, column=text):
                    sql = f"SELECT {text} FROM {table} p"
                    tree = sqlglot.parse_one(sql, read="spark")
                    for identifier in tree.find_all(exp.Identifier):
                        identifier.set("this", identifier.name.casefold())
                    with patch.object(sqlglot, "parse", return_value=[tree]):
                        result = validate_sql(sql, schema={table: {column: "STRING"}})
                    self.assertTrue(result["valid"], result)

    def test_real_spark_missing_column_still_rejected(self):
        for table, column in CASES:
            result = validate_sql(f"SELECT invented FROM {table}", schema={table: {column: "STRING"}})
            self.assertFalse(result["valid"])
            self.assertIn("unknown_column", [issue["code"] for issue in result["issues"]])

    def test_spark_backticks_follow_spark_case_insensitive_semantics(self):
        result = validate_sql("SELECT `inserted_dttm` FROM `HRPL_LM_SELFSERVICE_SRC`.`PRODUCT`",
                              schema={CASES[0][0]: {"INSERTED_DTTM": "STRING"}})
        self.assertTrue(result["valid"], result)

    def test_postgres_quoted_case_sensitive_semantics_preserved(self):
        schema = {"public.items": {"MixedCase": "TEXT"}}
        accepted = validate_sql('SELECT "MixedCase" FROM public.items', dialect="greenplum", schema=schema)
        rejected = validate_sql('SELECT "mixedcase" FROM public.items', dialect="greenplum", schema=schema)
        self.assertTrue(accepted["valid"], accepted)
        self.assertFalse(rejected["valid"])


if __name__ == "__main__":
    unittest.main()
