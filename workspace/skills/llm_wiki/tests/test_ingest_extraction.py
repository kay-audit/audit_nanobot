from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wiki_agent.errors import SecurityError, ValidationError
from wiki_agent.extraction import Extraction, ExtractionError, render
from wiki_agent.provider import FakeProvider
from wiki_agent.skills.ingest import run_ingest
from wiki_agent.workspace import Workspace, sha256_file

from tests.helpers import (
    create_workspace,
    settings,
    tree_digest,
    valid_knowledge_json,
)


WARNING = "OCR не выполнялся; часть текста могла быть потеряна."
LIMITATION = f"Ограничение извлечения: {WARNING}"


class IngestExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        create_workspace(self.root)
        self.source_relative = "raw/sources/new.pdf"
        self.source = self.root / self.source_relative
        self.source.write_bytes(b"fake-pdf-for-controller-test")
        self.workspace = Workspace(self.root)
        self.settings = settings(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_ingest_automatically_creates_extracted_copy(self) -> None:
        provider = FakeProvider([self._valid_payload()])
        wiki_before = tree_digest(self.root / "wiki")
        sources_before = self.workspace.snapshot_sources()

        with patch(
            "wiki_agent.skills.ingest.extract_document",
            return_value=Extraction("Extracted verified fact.", [WARNING]),
        ) as extractor:
            result = run_ingest(
                self.workspace,
                self.settings,
                provider,
                self.source_relative,
            )

        extractor.assert_called_once_with(self.source.resolve())
        extracted = self.root / "raw/extracted/new.pdf.md"
        content = extracted.read_text(encoding="utf-8")
        self.assertIn(
            'original_path: "raw/sources/new.pdf"',
            content,
        )
        self.assertIn(
            f'original_sha256: "{sha256_file(self.source)}"',
            content,
        )
        self.assertIn(WARNING, content)
        self.assertIn("Extracted verified fact.", content)
        self.assertIn(LIMITATION, provider.requests[0].user_prompt)
        self.assertIn(
            "UNTRUSTED_SOURCE_DATA_BEGIN",
            provider.requests[0].user_prompt,
        )
        self.assertEqual(result.extracted_path, "raw/extracted/new.pdf.md")
        self.assertTrue((self.root / result.proposal_path).is_file())
        self.assertEqual(wiki_before, tree_digest(self.root / "wiki"))
        self.workspace.assert_sources_unchanged(sources_before)

    def test_existing_extracted_copy_is_reused_without_overwrite(self) -> None:
        target = self.root / "raw/extracted/new.pdf.md"
        expected = render(
            self.source,
            Extraction("Existing extraction.", [WARNING]),
            original_path=self.source_relative,
            original_sha256=sha256_file(self.source),
        )
        target.write_text(expected, encoding="utf-8")
        provider = FakeProvider([self._valid_payload()])

        with patch(
            "wiki_agent.skills.ingest.extract_document",
            side_effect=AssertionError("extractor must not be called"),
        ):
            run_ingest(
                self.workspace,
                self.settings,
                provider,
                self.source_relative,
            )

        self.assertEqual(target.read_text(encoding="utf-8"), expected)
        self.assertIn("Existing extraction.", provider.requests[0].user_prompt)

    def test_extraction_error_stops_before_provider_and_proposal(self) -> None:
        provider = FakeProvider([])
        sources_before = self.workspace.snapshot_sources()

        with patch(
            "wiki_agent.skills.ingest.extract_document",
            side_effect=ExtractionError("broken document"),
        ):
            with self.assertRaisesRegex(
                ValidationError,
                "Автоматическое извлечение.*broken document",
            ):
                run_ingest(
                    self.workspace,
                    self.settings,
                    provider,
                    self.source_relative,
                )

        self.assertEqual(provider.requests, [])
        self.assertFalse(
            (self.root / "raw/extracted/new.pdf.md").exists()
        )
        self.assertEqual(list((self.root / "proposals").glob("*.md")), [])
        self.workspace.assert_sources_unchanged(sources_before)

    def test_source_mutation_during_extraction_is_not_published(self) -> None:
        provider = FakeProvider([])

        def mutate_source(_source: Path) -> Extraction:
            self.source.write_bytes(b"changed concurrently")
            return Extraction("Must not be published.", [WARNING])

        with patch(
            "wiki_agent.skills.ingest.extract_document",
            side_effect=mutate_source,
        ):
            with self.assertRaisesRegex(
                SecurityError,
                "raw/sources изменился",
            ):
                run_ingest(
                    self.workspace,
                    self.settings,
                    provider,
                    self.source_relative,
                )

        self.assertEqual(provider.requests, [])
        self.assertFalse(
            (self.root / "raw/extracted/new.pdf.md").exists()
        )

    def test_existing_copy_with_old_source_hash_is_rejected(self) -> None:
        target = self.root / "raw/extracted/new.pdf.md"
        target.write_text(
            render(
                self.source,
                Extraction("Old extraction.", [WARNING]),
                original_path=self.source_relative,
                original_sha256=sha256_file(self.source),
            ),
            encoding="utf-8",
        )
        self.source.write_bytes(b"a different source version")

        with self.assertRaisesRegex(ValidationError, "копия устарела"):
            self.workspace.source_text_path(self.source_relative)

    def _valid_payload(self) -> str:
        value = json.loads(valid_knowledge_json(self.root))
        value["source_limitations"] = [LIMITATION]
        return json.dumps(value, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
