"""Packaged synthetic JSON: real FAISS, deterministic fake vectors and LLM."""

from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tests.helpers import create_workspace, settings
from tests.test_semantic_search import CountingEmbedder
from wiki_agent.jira_confluence import card_documents, load_search_cards, prepare_jira_confluence, select_jira_sources
from wiki_agent.provider import FakeProvider
from wiki_agent.semantic import FaissPageIndex
from wiki_agent.skills.query import run_query
from wiki_agent.wiki import load_catalog
from wiki_agent.workspace import Workspace

EXAMPLES = Path(__file__).resolve().parents[1] / "examples/jira-json"


class ExampleJsonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        create_workspace(self.root)
        for source in EXAMPLES.glob("*.json"):
            shutil.copyfile(source, self.root / "raw/sources" / source.name)
        self.workspace = Workspace(self.root)

    def test_examples_scope_shared_page_limits_and_summary_reuse(self):
        sources = select_jira_sources(self.workspace, "demo")
        self.assertEqual(len(sources), 2)
        self.assertEqual(select_jira_sources(self.workspace, "demo-1001"), ("raw/sources/DEMO-1001.json",))
        self.assertEqual(select_jira_sources(self.workspace, "SHOP"), ("raw/sources/SHOP-2001.json",))
        before = self.workspace.snapshot_sources()
        result = prepare_jira_confluence(self.workspace, FakeProvider([
            json.dumps({"summary": "Вымышленные уведомления: 30 секунд, повторы через 1, 5 и 15 минут."}),
            json.dumps({"summary": "Вымышленный дефект DEMO-1002: дублирование event_id; Open."}),
            json.dumps({"summary": "Вымышленный исторический регламент: резерв на 15 минут, актуальность не подтверждена."}),
        ]))
        self.assertEqual((result.jira_count, result.confluence_count, result.llm_calls), (3, 4, 3))
        cards = {card.card_id: card for card in load_search_cards(self.workspace)}
        self.assertEqual(set(cards["confluence:demo:90001"].related_ids),
                         {"jira:demo:DEMO-1001", "jira:demo:DEMO-1002"})
        self.assertFalse(cards["confluence:demo:90004"].content_available)
        self.assertIn("text_outdated", " ".join(cards["confluence:demo:90003"].limitations))
        self.assertEqual(prepare_jira_confluence(self.workspace, FakeProvider([])).llm_calls, 0)
        self.assertEqual(self.workspace.snapshot_sources(), before)

    @unittest.skipUnless(importlib.util.find_spec("faiss"), "faiss-cpu не установлен")
    def test_real_faiss_cache_and_read_only_query_with_fake_llm(self):
        prepare_jira_confluence(self.workspace, FakeProvider(['{"summary":"Вымышленный регламент уведомлений."}']), key="DEMO-1001")
        options = replace(settings(self.root), query_search="faiss", embedding_model=CountingEmbedder.model_name)
        embedder = CountingEmbedder()
        index = FaissPageIndex(workspace_root=self.root, cache_dir=options.faiss_cache_dir,
                               model_cache_dir=options.embedding_cache_dir,
                               vector_cache_dir=options.embedding_vector_cache_dir,
                               model_name=embedder.model_name, embedder=embedder)
        documents = [document for document in load_catalog(self.workspace).documents
                     if document.path.startswith("wiki/pages/")]
        documents.extend(card_documents(self.workspace))
        self.assertEqual(index.build(documents).updated_embeddings, 3)
        repeat = index.build(documents)
        self.assertEqual((repeat.updated_embeddings, repeat.reused_embeddings), (0, 3))
        self.assertEqual(embedder.encoded_documents, 3)
        self.assertEqual(index.status(documents).state, "current")
        before = self.workspace.snapshot_sources()
        wiki_before = {str(path): path.read_bytes() for path in (self.root / "wiki").rglob("*") if path.is_file()}
        dry = run_query(self.workspace, options, FakeProvider([]), "Расскажи о DEMO-1001",
                        semantic_index=index, dry_run=True)
        self.assertTrue(dry.dry_run)
        self.assertTrue(dry.semantic_hits)
        self.assertIn("raw/sources/DEMO-1001.json", dry.selected_paths)
        provider = FakeProvider([
            "Вымышленная задача DEMO-1001: доставка уведомлений.",
            "Вымышленный регламент: доставка 30 секунд; повторы через 1, 5 и 15 минут.",
            "Тестовый ответ: три повторные попытки через 1, 5 и 15 минут.",
        ])
        answer = run_query(self.workspace, options, provider, "Расскажи о DEMO-1001", semantic_index=index)
        self.assertIn("1, 5 и 15", answer.answer)
        self.assertEqual(len(provider.requests), 3)
        self.assertEqual(self.workspace.snapshot_sources(), before)
        self.assertEqual(wiki_before, {str(path): path.read_bytes() for path in (self.root / "wiki").rglob("*") if path.is_file()})


if __name__ == "__main__":
    unittest.main()
