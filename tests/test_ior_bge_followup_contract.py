"""Cache-free contracts for the IOR-specific session semantic search."""
from __future__ import annotations

import importlib.util
import pickle
import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "workspace/skills/ior-analyzer/utils/bge_search_engine.py"
SPEC = importlib.util.spec_from_file_location("ior_bge_followup_contract", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
bge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bge)


class FakeIndex:
    def __init__(self, dimension):
        self.d = dimension
        self.vectors = np.empty((0, dimension), dtype="float32")

    def add(self, vectors):
        self.vectors = np.asarray(vectors, dtype="float32")

    @property
    def ntotal(self):
        return len(self.vectors)

    def search(self, query, k):
        scores = np.asarray(query) @ self.vectors.T
        order = np.argsort(-scores, axis=1)[:, :k]
        return np.take_along_axis(scores, order, axis=1), order


class FakeModel:
    def encode(self, values):
        return np.asarray([[0.0, 1.0] for _ in values], dtype="float32")


def fake_faiss_module():
    module = types.ModuleType("faiss")
    module.IndexFlatIP = FakeIndex
    module.normalize_L2 = lambda matrix: matrix.__setitem__(
        slice(None), matrix / np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-9)
    )
    return module


def test_default_paths_are_shared_pipeline_paths(monkeypatch):
    monkeypatch.delenv("IOR_RAG_CACHE_DIR", raising=False)
    monkeypatch.delenv("IOR_BGE_MODEL_PATH", raising=False)
    assert bge.cache_dir() == ROOT / "workspace/data_store/cache/caches_pipelines/cache_final"
    assert bge.model_dir() == ROOT / "workspace/data_store/cache/caches_pipelines/BAAI:bge-m3"
    assert bge.reranker_dir() == ROOT / "workspace/data_store/cache/caches_pipelines/bge-reranker-v2-m3"


def test_cache_final_metadata_and_memmap_contract(tmp_path):
    vectors = np.memmap(tmp_path / "embeddings.memmap", dtype="float32", mode="w+", shape=(2, 2))
    vectors[:] = [[1.0, 0.0], [0.0, 1.0]]
    vectors.flush()
    with (tmp_path / "embeddings_meta.pkl").open("wb") as stream:
        pickle.dump({"path": "/old/closed/path/embeddings.memmap", "dtype": "float32", "shape": (2, 2)}, stream)
    with (tmp_path / "meta.pkl").open("wb") as stream:
        pickle.dump({"documents": ["first", "second"], "doc_sids": ["EVE-1", "EVE-2"]}, stream)

    bge._cache_loaded = False
    bge.load_pipeline_meta(tmp_path)

    assert bge.doc_sids == ["EVE-1", "EVE-2"]
    assert bge.sid_to_index == {"EVE-1": 0, "EVE-2": 1}
    assert np.asarray(bge.embeddings).tolist() == [[1.0, 0.0], [0.0, 1.0]]


def test_session_index_reuses_ior_vectors_and_searches_with_threshold(monkeypatch):
    monkeypatch.setitem(sys.modules, "faiss", fake_faiss_module())
    monkeypatch.setattr(bge, "load_pipeline_meta", lambda path=None: None)
    monkeypatch.setattr(bge, "embeddings", np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype="float32"))
    monkeypatch.setattr(bge, "sid_to_index", {"EVE-1": 0, "EVE-2": 1})
    monkeypatch.setattr(bge, "id_to_index", {"EVE-1": 0, "EVE-2": 1})
    monkeypatch.setattr(bge, "get_bge_model", lambda: FakeModel())
    bge._SMALL_FAISS_SESSION_CACHE.clear()
    frame = pd.DataFrame({
        "incdnt_sid": ["EVE-1", "EVE-2"],
        "incdnt_full_descr_txt": ["first IOR", "second IOR"],
    })

    assert bge.build_and_cache_small_index("session", frame)
    results = bge.search_small_index("session", "semantic follow-up")

    assert results == [{"text": "second IOR", "id": "EVE-2", "score": 1.0}]
    assert bge.search_small_index("session", "semantic follow-up", threshold=1.1) == []


def test_global_pipeline_keeps_faiss_bm25_rrf_and_reranker(monkeypatch):
    monkeypatch.setitem(sys.modules, "faiss", fake_faiss_module())
    monkeypatch.setattr(bge, "get_bge_model", lambda: FakeModel())
    monkeypatch.setattr(bge, "documents", ["first IOR", "second IOR"])
    monkeypatch.setattr(bge, "doc_sids", ["EVE-1", "EVE-2"])
    monkeypatch.setattr(bge, "sid_to_index", {"EVE-1": 0, "EVE-2": 1})
    monkeypatch.setattr(bge, "incident_ids", [101, 102])
    monkeypatch.setattr(bge, "incident_dates", ["2025-01-01", "2026-01-01"])

    index = FakeIndex(2)
    index.add(np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype="float32"))

    class BM25:
        scores = {"num_docs": 2}
        def retrieve(self, _tokens, k, **_kwargs):
            return np.asarray([[1, 0]])[:, :k], np.asarray([[2.0, 1.0]])[:, :k]

    class Reranker:
        def predict(self, pairs, batch_size):
            assert batch_size == 32
            return np.asarray([3.0 if "second" in text else -3.0 for _, text in pairs])

    monkeypatch.setattr(bge, "get_reranker", lambda: Reranker())
    result = bge.search_pipeline(
        "semantic query", faiss_idx=index, bm25_shards=[(BM25(), 0)], top_k=1,
    )

    assert result.to_dict("records") == [{
        "incident_sid": "EVE-2", "incident_id": 102, "Текст_ИОР": "second IOR",
        "date": "2026-01-01", "score": pytest.approx(0.9525741268),
    }]


def test_long_ids_are_not_rounded_through_float():
    value = "1234567890123456789.0"
    assert "1234567890123456789" in bge.get_id_variations(value)
    assert 1234567890123456789 in bge.get_id_variations(value)
