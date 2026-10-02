from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from wiki_agent import WikiAgent
from wiki_agent.provider import FakeProvider

from tests.helpers import (
    create_workspace,
    settings,
    valid_knowledge_json,
)


class PublicApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        create_workspace(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_ingest_preview_and_apply_are_available_from_code(self) -> None:
        agent = WikiAgent(
            settings=settings(self.root),
            provider=FakeProvider([valid_knowledge_json(self.root)]),
        )

        proposed = agent.ingest("raw/sources/new.md")
        preview = agent.inspect_proposal(proposed.proposal_path)

        self.assertEqual(preview.path, proposed.proposal_path)
        self.assertIn("wiki/log.md", preview.diff)
        applied = agent.apply(preview.path, rebuild_index=False)
        self.assertIn(
            "wiki/sources/New Source.md",
            applied.changed_files,
        )
        self.assertIsNone(applied.index_status)
        self.assertIsNone(applied.index_error)

    def test_query_dry_run_is_available_from_code(self) -> None:
        agent = WikiAgent(
            settings=settings(self.root),
            provider=FakeProvider([]),
        )

        result = agent.query("Topic", dry_run=True)

        self.assertTrue(result.dry_run)
        self.assertIn("wiki/index.md", result.selected_paths)

    def test_jira_facade_is_available_from_code(self) -> None:
        agent = WikiAgent(
            settings=settings(self.root),
            provider=FakeProvider([]),
        )

        result = agent.jira.query("Topic", dry_run=True)

        self.assertTrue(result.dry_run)
        self.assertIn("wiki/index.md", result.selected_paths)


if __name__ == "__main__":
    unittest.main()
