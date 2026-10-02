from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wiki_agent import WikiAgent
from wiki_agent.errors import ConfigurationError
from wiki_agent.proposal import proposal_revision
from wiki_agent.provider import FakeProvider
from wiki_agent.semantic import IndexStatus
from wiki_agent.skills.ingest import run_ingest
from wiki_agent.workspace import Workspace

from tests.helpers import create_workspace, settings, valid_knowledge_json


class SuccessfulIndex:
    def __init__(self) -> None:
        self.page_paths: list[str] = []

    def build(self, pages):
        self.page_paths = [page.path for page in pages]
        return IndexStatus("current", "FAISS-индекс построен", len(pages))


class BrokenIndex:
    def build(self, pages):
        del pages
        raise ConfigurationError("embedding model unavailable")


class ApplyFaissTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        create_workspace(self.root)
        self.workspace = Workspace(self.root)
        self.settings = settings(self.root)
        self.agent = WikiAgent(settings=self.settings)
        provider = FakeProvider([valid_knowledge_json(self.root)])
        result = run_ingest(
            self.workspace,
            self.settings,
            provider,
            "raw/sources/new.md",
        )
        self.proposal_path = result.proposal_path
        content = self.root.joinpath(self.proposal_path).read_text(
            encoding="utf-8"
        )
        self.revision = proposal_revision(content)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_apply_rebuilds_faiss_after_wiki_transaction(self) -> None:
        index = SuccessfulIndex()

        with patch.object(
            self.agent,
            "_semantic_index",
            return_value=index,
        ):
            result = self.agent.apply(
                self.proposal_path,
                expected_revision=self.revision,
            )

        self.assertIsNone(result.index_error)
        self.assertEqual(result.index_status.state, "current")
        self.assertIn("wiki/pages/Topic.md", index.page_paths)
        self.assertIn(
            "status: applied",
            self.root.joinpath(self.proposal_path).read_text(
                encoding="utf-8"
            ),
        )

    def test_apply_path_argument_needs_no_second_input(self) -> None:
        index = SuccessfulIndex()

        with patch.object(
            self.agent,
            "_semantic_index",
            return_value=index,
        ):
            result = self.agent.apply(
                self.proposal_path,
            )

        self.assertIsNone(result.index_error)
        self.assertIn(
            "status: applied",
            self.root.joinpath(self.proposal_path).read_text(
                encoding="utf-8"
            ),
        )

    def test_faiss_failure_does_not_rollback_applied_wiki(self) -> None:
        with patch.object(
            self.agent,
            "_semantic_index",
            return_value=BrokenIndex(),
        ):
            result = self.agent.apply(
                self.proposal_path,
                expected_revision=self.revision,
            )

        self.assertIn(
            "ConfigurationError: embedding model unavailable",
            result.index_error,
        )
        self.assertIn(
            "status: applied",
            self.root.joinpath(self.proposal_path).read_text(
                encoding="utf-8"
            ),
        )
        self.assertIn(
            "New verified fact.",
            self.root.joinpath("wiki/pages/Topic.md").read_text(
                encoding="utf-8"
            ),
        )


if __name__ == "__main__":
    unittest.main()
