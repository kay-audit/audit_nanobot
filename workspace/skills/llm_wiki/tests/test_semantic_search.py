from __future__ import annotations

import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from wiki_agent.errors import ValidationError
from wiki_agent.semantic import FaissPageIndex, SentenceTransformerEmbedder
from wiki_agent.wiki import load_catalog
from wiki_agent.workspace import Workspace

from tests.helpers import create_workspace


FAISS_AVAILABLE = importlib.util.find_spec("faiss") is not None


class SentenceTransformerConfigurationTests(unittest.TestCase):
    def test_local_bge_uses_requested_device_and_disables_download(self) -> None:
        captured: dict[str, object] = {}

        class FakeSentenceTransformer:
            def __init__(self, name: str, **kwargs: object) -> None:
                captured["name"] = name
                captured.update(kwargs)

        sentence_transformers = types.ModuleType("sentence_transformers")
        sentence_transformers.SentenceTransformer = FakeSentenceTransformer
        transformers = types.ModuleType("transformers")
        transformers_utils = types.ModuleType("transformers.utils")
        transformers_utils.logging = types.SimpleNamespace(
            disable_progress_bar=lambda: None
        )
        transformers.utils = transformers_utils

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / ".cache/models"
            with patch.dict(
                sys.modules,
                {
                    "sentence_transformers": sentence_transformers,
                    "transformers": transformers,
                    "transformers.utils": transformers_utils,
                },
            ):
                SentenceTransformerEmbedder(
                    "/home/datalab/nfs/rag/bge/BAAI:bge-m3",
                    model_cache_dir=cache,
                    workspace_root=root,
                    allow_download=False,
                    device="cuda",
                )

        self.assertEqual(
            captured["name"],
            "/home/datalab/nfs/rag/bge/BAAI:bge-m3",
        )
        self.assertEqual(captured["device"], "cuda")
        self.assertTrue(captured["local_files_only"])
        self.assertFalse(captured["trust_remote_code"])


class FakeEmbedder:
    model_name = "fake-russian-model"

    def encode_documents(self, texts):
        import numpy as np

        return np.asarray(
            [
                [1.0, 0.0]
                if "Topic" in text
                else [0.0, 1.0]
                for text in texts
            ],
            dtype="float32",
        )

    def encode_query(self, text):
        import numpy as np

        return np.asarray(
            [1.0, 0.0] if "topic" in text.casefold() else [0.0, 1.0],
            dtype="float32",
        )


class CountingEmbedder(FakeEmbedder):
    model_name = "counting-model"

    def __init__(self) -> None:
        self.encoded_documents = 0

    def encode_documents(self, texts):
        self.encoded_documents += len(texts)
        return super().encode_documents(texts)


@unittest.skipUnless(FAISS_AVAILABLE, "faiss-cpu не установлен")
class SemanticSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        create_workspace(self.root)
        self.workspace = Workspace(self.root)
        self.pages = [
            item
            for item in load_catalog(self.workspace).documents
            if item.path.startswith("wiki/pages/")
        ]
        self.index = FaissPageIndex(
            workspace_root=self.workspace.root,
            cache_dir=self.workspace.root / ".cache/faiss",
            model_cache_dir=self.workspace.root / ".cache/models",
            model_name=FakeEmbedder.model_name,
            embedder=FakeEmbedder(),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_build_and_search(self) -> None:
        result = self.index.build(self.pages)
        self.assertEqual(result.document_count, 1)
        self.assertEqual(self.index.status(self.pages).state, "current")

        hits = self.index.search(
            self.pages,
            "topic question",
            limit=3,
            min_score=0.1,
        )
        self.assertEqual([hit.path for hit in hits], ["wiki/pages/Topic.md"])
        self.assertGreater(hits[0].score, 0.9)

    def test_build_reuses_unchanged_document_embeddings(self) -> None:
        embedder = CountingEmbedder()
        index = FaissPageIndex(
            workspace_root=self.workspace.root,
            cache_dir=self.workspace.root / ".cache/incremental-faiss",
            model_cache_dir=self.workspace.root / ".cache/models",
            vector_cache_dir=self.workspace.root / ".cache/embeddings",
            model_name=embedder.model_name,
            embedder=embedder,
        )

        first = index.build(self.pages)
        second = index.build(self.pages)

        self.assertEqual(first.updated_embeddings, 1)
        self.assertEqual(second.reused_embeddings, 1)
        self.assertEqual(second.updated_embeddings, 0)
        self.assertEqual(embedder.encoded_documents, 1)

        page_path = self.root / "wiki/pages/Topic.md"
        page_path.write_text(
            page_path.read_text(encoding="utf-8") + "\nChanged.\n",
            encoding="utf-8",
        )
        changed_pages = [
            item
            for item in load_catalog(self.workspace).documents
            if item.path.startswith("wiki/pages/")
        ]
        changed = index.build(changed_pages)
        self.assertEqual(changed.updated_embeddings, 1)

        other_embedder = CountingEmbedder()
        other_embedder.model_name = "other-counting-model"
        other_model_index = FaissPageIndex(
            workspace_root=self.workspace.root,
            cache_dir=self.workspace.root / ".cache/other-faiss",
            model_cache_dir=self.workspace.root / ".cache/models",
            vector_cache_dir=self.workspace.root / ".cache/embeddings",
            model_name=other_embedder.model_name,
            embedder=other_embedder,
        )
        other_model = other_model_index.build(changed_pages)
        self.assertEqual(other_model.updated_embeddings, 1)

    def test_index_becomes_stale_after_page_change(self) -> None:
        self.index.build(self.pages)
        page_path = self.root / "wiki/pages/Topic.md"
        page_path.write_text(
            page_path.read_text(encoding="utf-8") + "\nChanged.\n",
            encoding="utf-8",
        )
        changed_pages = [
            item
            for item in load_catalog(self.workspace).documents
            if item.path.startswith("wiki/pages/")
        ]
        status = self.index.status(changed_pages)
        self.assertEqual(status.state, "stale")
        self.assertIn("изменились", status.message)

    def test_build_does_not_change_wiki_or_sources(self) -> None:
        before_page = (self.root / "wiki/pages/Topic.md").read_bytes()
        before_source = (self.root / "raw/sources/source.md").read_bytes()
        self.index.build(self.pages)
        self.assertEqual(
            before_page,
            (self.root / "wiki/pages/Topic.md").read_bytes(),
        )
        self.assertEqual(
            before_source,
            (self.root / "raw/sources/source.md").read_bytes(),
        )

    def test_symlink_cache_is_rejected(self) -> None:
        outside = self.workspace.root / "outside"
        outside.mkdir()
        linked = self.workspace.root / "linked-cache"
        linked.symlink_to(outside, target_is_directory=True)
        unsafe = FaissPageIndex(
            workspace_root=self.workspace.root,
            cache_dir=linked,
            model_cache_dir=self.root / ".cache/models",
            model_name=FakeEmbedder.model_name,
            embedder=FakeEmbedder(),
        )
        with self.assertRaisesRegex(ValidationError, "symlink"):
            unsafe.build(self.pages)


if __name__ == "__main__":
    unittest.main()
