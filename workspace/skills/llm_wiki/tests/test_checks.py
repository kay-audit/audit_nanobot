from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from wiki_agent.checks import run_checks
from wiki_agent.workspace import Workspace

from tests.helpers import create_workspace, write


class CheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        create_workspace(self.root)
        self.workspace = Workspace(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_clean_fixture_has_no_issues(self) -> None:
        self.assertEqual(run_checks(self.workspace), [])

    def test_broken_link_is_error(self) -> None:
        page = (self.root / "wiki/pages/Topic.md").read_text(encoding="utf-8")
        write(
            self.root,
            "wiki/pages/Topic.md",
            page.replace("[[Source]]", "[[Missing]]", 1),
        )
        issues = run_checks(self.workspace)
        self.assertTrue(
            any(
                issue.level == "error" and issue.title == "Битая Wiki-ссылка"
                for issue in issues
            )
        )

    def test_duplicate_title_is_error(self) -> None:
        content = (self.root / "wiki/pages/Topic.md").read_text(
            encoding="utf-8"
        )
        write(self.root, "wiki/pages/Another.md", content)
        issues = run_checks(self.workspace)
        self.assertTrue(
            any(issue.title == "Дублирующийся title" for issue in issues)
        )

    def test_source_outside_raw_is_error(self) -> None:
        card = (self.root / "wiki/sources/Source.md").read_text(
            encoding="utf-8"
        )
        write(
            self.root,
            "wiki/sources/Source.md",
            card.replace(
                "source_path: raw/sources/source.md",
                "source_path: wiki/index.md",
            ),
        )
        issues = run_checks(self.workspace)
        self.assertTrue(
            any(issue.title == "Некорректный source_path" for issue in issues)
        )

    def test_updated_at_must_be_in_page_front_matter(self) -> None:
        path = self.root / "wiki/pages/Topic.md"
        content = path.read_text(encoding="utf-8").replace(
            "updated_at: 2026-07-25\n",
            "",
            1,
        )
        path.write_text(
            content + "\nupdated_at: 2026-07-25\n",
            encoding="utf-8",
        )
        issues = run_checks(self.workspace)
        self.assertTrue(
            any(
                issue.title == "Некорректная дата updated_at"
                and issue.path == "wiki/pages/Topic.md"
                for issue in issues
            )
        )

    def test_updated_at_must_be_in_source_front_matter(self) -> None:
        path = self.root / "wiki/sources/Source.md"
        content = path.read_text(encoding="utf-8").replace(
            "updated_at: 2026-07-25\n",
            "",
            1,
        )
        path.write_text(
            content + "\nupdated_at: 2026-07-25\n",
            encoding="utf-8",
        )
        issues = run_checks(self.workspace)
        self.assertTrue(
            any(
                issue.title == "Некорректная дата updated_at"
                and issue.path == "wiki/sources/Source.md"
                for issue in issues
            )
        )

    def test_page_sources_section_must_link_a_card(self) -> None:
        path = self.root / "wiki/pages/Topic.md"
        content = path.read_text(encoding="utf-8").replace(
            "## Источники\n\n- [[Source]]",
            "## Источники\n\nНет.",
        )
        path.write_text(content, encoding="utf-8")
        issues = run_checks(self.workspace)
        self.assertTrue(
            any(
                issue.title == "Нет карточки в разделе источников"
                for issue in issues
            )
        )

    def test_small_thematic_cycle_without_exit_is_reported(self) -> None:
        template = """---
title: {title}
category: concepts
aliases: []
tags: []
status: active
updated_at: 2026-07-25
---

# {title}

Связь с [[{other}]].

## Источники

- [[Source]]
"""
        write(
            self.root,
            "wiki/pages/A.md",
            template.format(title="A", other="B"),
        )
        write(
            self.root,
            "wiki/pages/B.md",
            template.format(title="B", other="A"),
        )
        index = self.root / "wiki/index.md"
        index.write_text(
            index.read_text(encoding="utf-8") + "- [[A]]\n",
            encoding="utf-8",
        )
        issues = run_checks(self.workspace)
        self.assertTrue(
            any(
                issue.title
                == "Небольшой цикл тематических ссылок без выхода"
                for issue in issues
            )
        )
