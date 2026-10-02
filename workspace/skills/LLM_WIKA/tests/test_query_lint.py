from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from wiki_agent.provider import FakeProvider, StubProvider
from wiki_agent.semantic import SemanticHit
from wiki_agent.skills.lint import run_lint
from wiki_agent.skills.query import run_query
from wiki_agent.workspace import Workspace

from tests.helpers import create_workspace, settings, tree_digest


class QueryAndLintTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        create_workspace(self.root)
        self.workspace = Workspace(self.root)
        self.settings = settings(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_query_does_not_write(self) -> None:
        provider = FakeProvider(["## Краткий вывод\n\nОтвет.\n"])
        before = tree_digest(self.root)
        result = run_query(
            self.workspace,
            self.settings,
            provider,
            "Что такое Topic?",
        )
        self.assertIn("Ответ", result.answer or "")
        self.assertEqual(before, tree_digest(self.root))
        self.assertEqual(provider.requests[0].operation, "query")

    def test_query_can_optionally_save_markdown_report(self) -> None:
        provider = FakeProvider(["## Краткий вывод\n\nОтвет.\n"])
        result = run_query(
            self.workspace,
            self.settings,
            provider,
            "Что такое Topic?",
            save_markdown=True,
        )

        self.assertIsNotNone(result.answer_path)
        report = self.root.joinpath(result.answer_path or "").read_text(
            encoding="utf-8"
        )
        self.assertIn("Что такое Topic?", report)
        self.assertIn("Ответ.", report)

    def test_query_dry_run_never_calls_provider(self) -> None:
        provider = FakeProvider([])
        result = run_query(
            self.workspace,
            self.settings,
            provider,
            "Topic",
            dry_run=True,
        )
        self.assertTrue(result.dry_run)
        self.assertIn("wiki/index.md", result.selected_paths)
        self.assertEqual(provider.requests, [])

    def test_query_does_not_send_orphan_page(self) -> None:
        orphan = """---
title: Secret Orphan
category: concepts
aliases: []
tags: []
status: active
updated_at: 2026-07-25
---

# Secret Orphan

secret-marker

## Источники

- [[Source]]
"""
        (self.root / "wiki/pages/Secret Orphan.md").write_text(
            orphan, encoding="utf-8"
        )
        provider = FakeProvider(["answer"])
        result = run_query(
            self.workspace,
            self.settings,
            provider,
            "Topic",
        )
        self.assertNotIn("wiki/pages/Secret Orphan.md", result.selected_paths)
        self.assertNotIn(
            "secret-marker", provider.requests[0].user_prompt
        )

    def test_faiss_seed_does_not_depend_on_index_reachability(self) -> None:
        orphan = """---
title: Semantic Orphan
category: concepts
aliases: []
tags: []
status: active
updated_at: 2026-07-26
---

# Semantic Orphan

Meaning found by embeddings.

## Источники

- [[Source]]
"""
        path = "wiki/pages/Semantic Orphan.md"
        (self.root / path).write_text(orphan, encoding="utf-8")

        class FakeSemanticIndex:
            def search(self, pages, question, *, limit, min_score):
                del pages, question, limit, min_score
                return [SemanticHit(path, "Semantic Orphan", 0.91)]

        provider = FakeProvider(["answer"])
        faiss_settings = replace(self.settings, query_search="faiss")
        result = run_query(
            self.workspace,
            faiss_settings,
            provider,
            "unrelated wording",
            semantic_index=FakeSemanticIndex(),
        )
        self.assertIn(path, result.selected_paths)
        self.assertEqual(result.semantic_hits[0].score, 0.91)
        self.assertIn(
            "Meaning found by embeddings.",
            provider.requests[0].user_prompt,
        )

    def test_technical_lint_only_creates_report(self) -> None:
        before = tree_digest(self.root, exclude=("reports/lint",))
        first = run_lint(
            self.workspace,
            self.settings,
            StubProvider(),
            technical_only=True,
        )
        second = run_lint(
            self.workspace,
            self.settings,
            StubProvider(),
            technical_only=True,
        )
        self.assertNotEqual(first.report_path, second.report_path)
        self.assertEqual(before, tree_digest(self.root, exclude=("reports/lint",)))
        self.assertEqual(first.counts["error"], 0)
        self.assertFalse(first.semantic_checked)

    def test_query_discloses_partial_context(self) -> None:
        provider = FakeProvider(["answer"])
        limited = replace(self.settings, max_context_chars=300)
        result = run_query(
            self.workspace,
            limited,
            provider,
            "Topic",
        )
        self.assertTrue(result.selected_paths)
        prompt = provider.requests[0].user_prompt
        self.assertIn("[TRUNCATED", prompt)
        self.assertIn("Частично переданы", prompt)

    def test_semantic_lint_reports_partial_coverage(self) -> None:
        provider = FakeProvider(["Смысловой обзор."])
        limited = replace(self.settings, max_context_chars=300)
        result = run_lint(
            self.workspace,
            limited,
            provider,
            technical_only=False,
        )
        report = self.root.joinpath(result.report_path).read_text(
            encoding="utf-8"
        )
        self.assertIn("Частично переданы", report)
        self.assertIn("Не переданы из-за лимита", report)
        self.assertIn("[TRUNCATED", provider.requests[0].user_prompt)

    def test_semantic_lint_includes_primary_source_excerpt(self) -> None:
        provider = FakeProvider(["Смысловой обзор."])
        run_lint(
            self.workspace,
            self.settings,
            provider,
            technical_only=False,
        )
        prompt = provider.requests[0].user_prompt
        self.assertIn(
            "===== FILE: raw/sources/source.md =====",
            prompt,
        )
