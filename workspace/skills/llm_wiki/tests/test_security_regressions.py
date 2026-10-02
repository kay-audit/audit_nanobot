from __future__ import annotations

import base64
import stat
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import wiki_agent.proposal as proposal_module
from wiki_agent.errors import (
    ProposalError,
    SecurityError,
    ValidationError,
)
from wiki_agent.models import LLMResponse
from wiki_agent.proposal import (
    CHANGESET_BEGIN,
    apply_proposal,
    extract_json_object,
    load_changeset_from_proposal,
)
from wiki_agent.provider import FakeProvider
from wiki_agent.skills.ingest import run_ingest
from wiki_agent.workspace import Workspace

from tests.helpers import (
    create_workspace,
    settings,
    valid_knowledge_json,
)


class SecurityRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        create_workspace(self.root)
        self.workspace = Workspace(self.root)
        self.settings = settings(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_source_mutation_during_provider_call_leaves_no_proposal(self) -> None:
        response = valid_knowledge_json(self.root)
        source = self.root / "raw/sources/new.md"

        class MutatingProvider:
            name = "mutating"

            def complete(self, request: object) -> LLMResponse:
                del request
                source.write_text("# Mutated\n", encoding="utf-8")
                return LLMResponse(response)

        with self.assertRaises(SecurityError):
            run_ingest(
                self.workspace,
                self.settings,
                MutatingProvider(),
                "raw/sources/new.md",
            )
        self.assertEqual(list((self.root / "proposals").glob("*.md")), [])

    def test_truncated_source_requires_partial_card_and_is_disclosed(self) -> None:
        source_text = "verified-data " * 2_000
        self.root.joinpath("raw/sources/new.md").write_text(
            source_text, encoding="utf-8"
        )
        limited = replace(self.settings, max_context_chars=20_000)
        result = run_ingest(
            self.workspace,
            limited,
            FakeProvider([valid_knowledge_json(self.root)]),
            "raw/sources/new.md",
        )
        proposal = self.root.joinpath(result.proposal_path).read_text(
            encoding="utf-8"
        )
        self.assertIn("передал модели только начало", proposal)
        self.assertIn("status: partial", proposal)

    def test_apply_preserves_mode(self) -> None:
        index = self.root / "wiki/index.md"
        index.chmod(0o640)
        result = run_ingest(
            self.workspace,
            self.settings,
            FakeProvider([valid_knowledge_json(self.root)]),
            "raw/sources/new.md",
        )
        apply_proposal(
            self.workspace,
            result.proposal_path,
            confirmed_path=result.proposal_path,
        )
        self.assertEqual(stat.S_IMODE(index.stat().st_mode), 0o640)

    def test_commit_cas_preserves_concurrent_edit(self) -> None:
        result = run_ingest(
            self.workspace,
            self.settings,
            FakeProvider([valid_knowledge_json(self.root)]),
            "raw/sources/new.md",
        )
        original = proposal_module._transactional_replace
        concurrent = "# concurrent edit\n"

        def mutate_then_commit(*args: object, **kwargs: object) -> object:
            self.root.joinpath("wiki/index.md").write_text(
                concurrent, encoding="utf-8"
            )
            return original(*args, **kwargs)

        with patch.object(
            proposal_module,
            "_transactional_replace",
            side_effect=mutate_then_commit,
        ):
            with self.assertRaises(ProposalError):
                apply_proposal(
                    self.workspace,
                    result.proposal_path,
                    confirmed_path=result.proposal_path,
                )
        self.assertEqual(
            self.root.joinpath("wiki/index.md").read_text(encoding="utf-8"),
            concurrent,
        )
        self.assertFalse(
            self.root.joinpath("wiki/sources/New Source.md").exists()
        )

    def test_apply_requires_status_in_first_front_matter(self) -> None:
        result = run_ingest(
            self.workspace,
            self.settings,
            FakeProvider([valid_knowledge_json(self.root)]),
            "raw/sources/new.md",
        )
        proposal_path = self.root / result.proposal_path
        content = proposal_path.read_text(encoding="utf-8")
        content = content.replace("status: proposed\n", "", 1)
        content += "\nstatus: proposed\n"
        proposal_path.write_text(content, encoding="utf-8")
        with self.assertRaises(ProposalError):
            apply_proposal(
                self.workspace,
                result.proposal_path,
                confirmed_path=result.proposal_path,
            )
        self.assertFalse(
            self.root.joinpath("wiki/sources/New Source.md").exists()
        )

    def test_case_alias_of_existing_wiki_file_is_rejected(self) -> None:
        with self.assertRaises(SecurityError):
            self.workspace.validate_ingest_target(
                "wiki/pages/topic.md"
            )

    def test_create_if_absent_never_overwrites_parallel_writer(self) -> None:
        barrier = threading.Barrier(3)
        successes: list[str] = []
        failures: list[Exception] = []

        def writer(content: str) -> None:
            barrier.wait()
            try:
                self.workspace.write_text(
                    "proposals/race.md",
                    content,
                    allowed_roots=("proposals",),
                    must_not_exist=True,
                )
                successes.append(content)
            except Exception as exc:
                failures.append(exc)

        first = threading.Thread(target=writer, args=("first",))
        second = threading.Thread(target=writer, args=("second",))
        first.start()
        second.start()
        barrier.wait()
        first.join()
        second.join()

        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ValidationError)
        self.assertEqual(
            self.root.joinpath("proposals/race.md").read_text(
                encoding="utf-8"
            ),
            successes[0],
        )

    def test_malformed_encoded_changeset_has_controlled_error(self) -> None:
        encoded = base64.b64encode(
            b'{"version":"bad"}'
        ).decode("ascii")
        content = (
            f"{CHANGESET_BEGIN}\n{encoded}\n```\n"
        )
        with self.assertRaises(ProposalError):
            load_changeset_from_proposal(content)

    def test_model_json_prefix_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            extract_json_object('explanation\n{"changes":[]}')

    def test_extracted_copy_must_match_original(self) -> None:
        self.root.joinpath("raw/sources/paper.pdf").write_bytes(b"%PDF")
        self.root.joinpath("raw/extracted/paper.pdf.md").write_text(
            '---\noriginal_path: "raw/sources/other.pdf"\n---\n',
            encoding="utf-8",
        )
        with self.assertRaises(ValidationError):
            self.workspace.source_text_path("raw/sources/paper.pdf")

    def test_control_character_in_source_name_is_rejected(self) -> None:
        relative = "raw/sources/bad\nname.md"
        self.root.joinpath(relative).write_text("data", encoding="utf-8")
        with self.assertRaises(SecurityError):
            self.workspace.source_path(relative)


if __name__ == "__main__":
    unittest.main()
