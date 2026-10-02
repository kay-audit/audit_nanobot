from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from wiki_agent.errors import ValidationError
from wiki_agent.skills.ingest import IngestResult
from wiki_agent.watcher import SourceInboxWatcher
from wiki_agent.workspace import Workspace

from tests.helpers import create_workspace


class SourceInboxWatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        create_workspace(self.root)
        self.workspace = Workspace(self.root)
        self.calls: list[str] = []

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _processor(self, path: str) -> IngestResult:
        self.calls.append(path)
        return IngestResult(
            proposal_path="proposals/new.md",
            selected_paths=("wiki/index.md",),
        )

    def test_existing_sources_are_ignored_by_default(self) -> None:
        watcher = SourceInboxWatcher(
            self.workspace,
            self._processor,
            settle_seconds=0,
        )
        self.assertEqual(watcher.initial_count, 2)
        self.assertEqual(watcher.poll(now=0), [])
        self.assertEqual(watcher.poll(now=1), [])
        self.assertEqual(self.calls, [])

    def test_new_file_is_processed_only_after_it_is_stable(self) -> None:
        watcher = SourceInboxWatcher(
            self.workspace,
            self._processor,
            settle_seconds=2,
        )
        source = self.root / "raw/sources/new-drop.pdf"
        source.write_bytes(b"part")

        self.assertEqual(watcher.poll(now=0), [])
        source.write_bytes(b"complete file")
        self.assertEqual(watcher.poll(now=1), [])
        self.assertEqual(watcher.poll(now=2), [])
        events = watcher.poll(now=3)

        self.assertEqual(self.calls, ["raw/sources/new-drop.pdf"])
        self.assertEqual(len(events), 1)
        self.assertEqual(
            events[0].result.proposal_path if events[0].result else None,
            "proposals/new.md",
        )
        self.assertEqual(watcher.poll(now=10), [])

    def test_failed_file_is_retried_only_after_change(self) -> None:
        calls: list[str] = []

        def broken(path: str) -> IngestResult:
            calls.append(path)
            raise ValidationError("temporary failure")

        watcher = SourceInboxWatcher(
            self.workspace,
            broken,
            settle_seconds=0,
        )
        source = self.root / "raw/sources/broken.pdf"
        source.write_bytes(b"first")

        watcher.poll(now=0)
        events = watcher.poll(now=1)
        self.assertEqual(len(events), 1)
        self.assertIn("temporary failure", events[0].error or "")
        watcher.poll(now=2)
        self.assertEqual(len(calls), 1)

        source.write_bytes(b"second version")
        watcher.poll(now=3)
        watcher.poll(now=4)
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
