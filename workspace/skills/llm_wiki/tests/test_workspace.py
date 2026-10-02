from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from wiki_agent.errors import SecurityError
from wiki_agent.workspace import Workspace

from tests.helpers import create_workspace


class WorkspaceSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        create_workspace(self.root)
        self.workspace = Workspace(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_rejects_parent_traversal(self) -> None:
        with self.assertRaises(SecurityError):
            self.workspace.resolve("../../etc/passwd")

    def test_rejects_absolute_path(self) -> None:
        with self.assertRaises(SecurityError):
            self.workspace.resolve("/etc/passwd")

    def test_source_must_be_inside_raw_sources(self) -> None:
        with self.assertRaises(SecurityError):
            self.workspace.source_path("wiki/index.md")

    def test_ingest_cannot_target_source_or_schema(self) -> None:
        for path in (
            "raw/sources/source.md",
            "raw/extracted/source.md",
            "schema/taxonomy.md",
            ".env",
        ):
            with self.subTest(path=path):
                with self.assertRaises(SecurityError):
                    self.workspace.validate_ingest_target(path)

    def test_rejects_nested_and_noncanonical_wiki_targets(self) -> None:
        for path in (
            "wiki/pages/nested/Topic.md",
            "wiki/pages//Topic.md",
            "./wiki/pages/Topic.md",
        ):
            with self.subTest(path=path):
                with self.assertRaises(SecurityError):
                    self.workspace.validate_ingest_target(path)

    def test_raw_snapshot_detects_mutation(self) -> None:
        before = self.workspace.snapshot_sources()
        (self.root / "raw/sources/source.md").write_text(
            "mutated", encoding="utf-8"
        )
        with self.assertRaises(SecurityError):
            self.workspace.assert_sources_unchanged(before)

    def test_rejects_symlink_as_ingest_target(self) -> None:
        link = self.root / "wiki/pages/Alias.md"
        link.symlink_to(self.root / "wiki/pages/Topic.md")
        with self.assertRaises(SecurityError):
            self.workspace.validate_ingest_target("wiki/pages/Alias.md")
