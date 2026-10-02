from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from wiki_agent.proposal import load_changeset_from_proposal
from wiki_agent.provider import FakeProvider
from wiki_agent.skills.ingest import run_ingest
from wiki_agent.workspace import Workspace

from tests.helpers import create_workspace, settings, tree_digest


def knowledge_json(*, title: str = "New Topic") -> str:
    return json.dumps(
        {
            "protocol": "knowledge-v1",
            "summary": "Добавить подтверждённые знания.",
            "source_title": "New Knowledge Source",
            "source_summary": "Краткий проверяемый материал.",
            "source_limitations": ["Один небольшой учебный материал."],
            "conflicts": [],
            "topics": [
                {
                    "title": title,
                    "summary": "Самостоятельная полезная тема.",
                    "claims": [
                        "Источник содержит новый подтверждённый факт",
                        "Факт имеет ограниченную область применимости",
                    ],
                    "aliases": [],
                    "tags": ["test"],
                    "category": "concepts",
                    "index_section": "Тестовые знания",
                    "related_topics": (
                        ["Topic"] if title != "Topic" else []
                    ),
                    "limitations": ["Нужна проверка на других источниках."],
                }
            ],
        },
        ensure_ascii=False,
    )


class KnowledgeIngestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        create_workspace(self.root)
        self.workspace = Workspace(self.root)
        self.settings = settings(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_controller_builds_all_mechanical_files(self) -> None:
        wiki_before = tree_digest(self.root / "wiki")
        provider = FakeProvider([knowledge_json()])

        result = run_ingest(
            self.workspace,
            self.settings,
            provider,
            "raw/sources/new.md",
        )

        self.assertEqual(wiki_before, tree_digest(self.root / "wiki"))
        proposal = self.root.joinpath(result.proposal_path)
        changeset = load_changeset_from_proposal(
            proposal.read_text(encoding="utf-8")
        )
        by_path = {change.path: change for change in changeset.changes}
        self.assertEqual(
            set(by_path),
            {
                "wiki/pages/New Topic.md",
                "wiki/sources/New Knowledge Source.md",
                "wiki/index.md",
                "wiki/log.md",
            },
        )
        card = by_path["wiki/sources/New Knowledge Source.md"]
        self.assertIn(
            'source_path: "raw/sources/new.md"',
            card.after_content,
        )
        self.assertIn(
            f"`{result.proposal_path}`",
            by_path["wiki/log.md"].after_content,
        )
        self.assertTrue(
            by_path["wiki/index.md"].after_content.startswith(
                by_path["wiki/index.md"].before_content or ""
            )
        )
        self.assertNotIn(
            '"after_content":',
            provider.requests[0].system_prompt,
        )
        self.assertNotIn(
            '"source_path":',
            provider.requests[0].system_prompt,
        )

    def test_existing_page_is_only_extended(self) -> None:
        old_page = self.root.joinpath(
            "wiki/pages/Topic.md"
        ).read_text(encoding="utf-8")

        result = run_ingest(
            self.workspace,
            self.settings,
            FakeProvider([knowledge_json(title="Topic")]),
            "raw/sources/new.md",
        )

        changeset = load_changeset_from_proposal(
            self.root.joinpath(result.proposal_path).read_text(
                encoding="utf-8"
            )
        )
        page = next(
            change
            for change in changeset.changes
            if change.path == "wiki/pages/Topic.md"
        )
        self.assertEqual(page.action, "update")
        self.assertTrue(
            all(line in page.after_content for line in old_page.splitlines())
        )
        self.assertIn("[[New Knowledge Source]]", page.after_content)
        self.assertEqual(
            self.root.joinpath("wiki/pages/Topic.md").read_text(
                encoding="utf-8"
            ),
            old_page,
        )

    def test_existing_page_path_forces_update(self) -> None:
        old_path = self.root / "wiki/pages/Topic.md"
        new_path = self.root / "wiki/pages/Path Topic.md"
        old_content = old_path.read_text(encoding="utf-8")
        new_path.write_text(old_content, encoding="utf-8")
        old_path.unlink()

        result = run_ingest(
            self.workspace,
            self.settings,
            FakeProvider([knowledge_json(title="Path Topic")]),
            "raw/sources/new.md",
        )

        changeset = load_changeset_from_proposal(
            self.root.joinpath(result.proposal_path).read_text(encoding="utf-8")
        )
        page = next(
            change
            for change in changeset.changes
            if change.path == "wiki/pages/Path Topic.md"
        )
        self.assertEqual(page.action, "update")
        self.assertEqual(page.before_content, old_content)

    def test_ambiguous_aliases_are_removed_from_new_pages(self) -> None:
        value = json.loads(knowledge_json(title="First Topic"))
        first = value["topics"][0]
        first["aliases"] = ["Shared alias"]
        second = dict(first)
        second["title"] = "Second Topic"
        second["aliases"] = ["Shared alias"]
        value["topics"] = [first, second]

        result = run_ingest(
            self.workspace,
            self.settings,
            FakeProvider([json.dumps(value, ensure_ascii=False)]),
            "raw/sources/new.md",
        )

        changeset = load_changeset_from_proposal(
            self.root.joinpath(result.proposal_path).read_text(encoding="utf-8")
        )
        pages = [
            change
            for change in changeset.changes
            if change.path.startswith("wiki/pages/")
        ]
        self.assertEqual(len(pages), 2)
        self.assertTrue(
            all("Shared alias" not in page.after_content for page in pages)
        )
        self.assertTrue(
            any("Shared alias" in conflict for conflict in changeset.conflicts)
        )

    def test_source_title_collision_is_resolved_locally(self) -> None:
        value = json.loads(knowledge_json())
        value["source_title"] = "New Topic"

        result = run_ingest(
            self.workspace,
            self.settings,
            FakeProvider([json.dumps(value, ensure_ascii=False)]),
            "raw/sources/new.md",
        )

        changeset = load_changeset_from_proposal(
            self.root.joinpath(result.proposal_path).read_text(
                encoding="utf-8"
            )
        )
        source_card = next(
            change
            for change in changeset.changes
            if change.path.startswith("wiki/sources/")
        )
        self.assertNotEqual(
            source_card.path,
            "wiki/sources/New Topic.md",
        )
        self.assertIn(
            'source_path: "raw/sources/new.md"',
            source_card.after_content,
        )

    def test_knowledge_repair_never_requests_changeset(self) -> None:
        invalid = json.dumps(
            {"summary": "Нет массива тем"},
            ensure_ascii=False,
        )
        provider = FakeProvider([invalid, knowledge_json()])

        run_ingest(
            self.workspace,
            self.settings,
            provider,
            "raw/sources/new.md",
        )

        self.assertEqual(len(provider.requests), 2)
        repair = provider.requests[1]
        self.assertIn("PREVIOUS_INVALID_KNOWLEDGE_BEGIN", repair.user_prompt)
        self.assertIn("не возвращай ChangeSet", repair.system_prompt)
        self.assertNotIn(
            "EXACT_CONTROLLER_BASELINES_BEGIN",
            repair.user_prompt,
        )


if __name__ == "__main__":
    unittest.main()
