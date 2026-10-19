from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from lib.services.kb_store import KbStore, KbStoreError
from workspace.skills.sql_assistant.scripts import build_index as module


class PagedProvider:
    def __init__(self, count):
        self.count = count
        self.calls = []

    def execute_readonly(self, sql, params=None, max_rows=1000):
        self.calls.append((sql, params, max_rows))
        if "COUNT(*)" in sql:
            return {"columns": ["row_count", "max_updated_at", "tables_max_updated_at"], "rows": [(self.count, "now", "now")]}
        start = params[0] if len(params) == 2 else 0
        size = params[-1]
        rows = [(identity, 1, "db.table", f"c{identity}", "STRING", "description") for identity in range(start + 1, min(self.count, start + size) + 1)]
        return {"columns": ["id", "table_id", "table_name", "column_name", "data_type", "description"], "rows": rows}


class TestCompleteIndex(unittest.TestCase):
    def test_105910_columns_read_without_cutoff_and_without_models(self):
        provider = PagedProvider(105910)
        models = SimpleNamespace(embed=Mock(side_effect=AssertionError("columns must be lexical")))
        with patch.object(module, "build_index", return_value={"count": 105910}) as publish:
            result = module.build_corpus(KbStore(provider), "unused", "columns", models=models)
        self.assertEqual(result["count"], 105910)
        self.assertEqual(len(publish.call_args.args[2]), 105910)
        self.assertFalse(publish.call_args.kwargs["dense_vectors"])
        self.assertTrue(any(len(params) == 2 for sql, params, _ in provider.calls if "COUNT(*)" not in sql))
        models.embed.assert_not_called()

    def test_incomplete_source_fails_before_publish(self):
        store = SimpleNamespace(current_source_signature=Mock(return_value='{"row_count":2}'), complete_corpus_frame=Mock(return_value=[{"id": 1}]))
        with patch.object(module, "build_index") as publish:
            with self.assertRaisesRegex(ValueError, "incomplete"):
                module.build_corpus(store, "unused", "tables")
        publish.assert_not_called()

    def test_changed_source_fails_before_publish(self):
        store = SimpleNamespace(
            current_source_signature=Mock(side_effect=['{"row_count":1,"version":1}', '{"row_count":1,"version":2}']),
            complete_corpus_frame=Mock(return_value=[{"id": 1, "table_name": "db.table"}]),
        )
        with patch.object(module, "build_index") as publish:
            with self.assertRaisesRegex(ValueError, "changed"):
                module.build_corpus(store, "unused", "tables")
        publish.assert_not_called()

    def test_dense_embedding_ids_follow_document_order(self):
        store = SimpleNamespace(
            current_source_signature=Mock(return_value='{"row_count":2}'),
            complete_corpus_frame=Mock(return_value=[{"id": 7, "table_name": "a"}, {"id": 8, "table_name": "b"}]),
            rows_hash=Mock(return_value="hash"),
        )
        models = SimpleNamespace(embed=Mock(return_value=[[1.0, 0.0], [0.0, 1.0]]))
        with patch.object(module, "build_index") as publish:
            module.build_corpus(store, "unused", "tables", models=models)
        self.assertEqual(publish.call_args.kwargs["dense_vectors"], {"7": [1.0, 0.0], "8": [0.0, 1.0]})

    def test_pagination_rejects_nonadvancing_provider(self):
        provider = SimpleNamespace(execute_readonly=Mock(return_value={"columns": ["id"], "rows": [(1,)]}))
        with self.assertRaisesRegex(KbStoreError, "pagination"):
            KbStore(provider).complete_corpus_frame("tables", page_size=1)

    def test_builder_closes_provider_if_models_unavailable(self):
        connection = SimpleNamespace(close=Mock())
        with patch.object(module, "DuckDbProvider", return_value=SimpleNamespace(connection=connection)), patch.object(module, "initialize_settings", side_effect=ValueError("profile missing")):
            with self.assertRaisesRegex(ValueError, "profile"):
                module.main(["--duckdb", "fake", "--index-root", "fake"])
        connection.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
