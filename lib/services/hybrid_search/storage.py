from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .core import HybridDocument, HybridIndex, tokenize

_CACHE_LOCK = threading.Lock()
_INDEX_CACHE: dict[tuple[str, str, str, str], tuple[HybridIndex, dict[str, Any]]] = {}


def _build_bm25(documents: Sequence[HybridDocument], output: Path) -> None:
    import bm25s

    tokenized = [tokenize(document.text) for document in documents]
    vocabulary: dict[str, int] = {}
    for tokens in tokenized:
        for token in tokens:
            vocabulary.setdefault(token, len(vocabulary))
    token_ids = [[vocabulary[token] for token in tokens] for tokens in tokenized]
    retriever = bm25s.BM25()
    retriever.index((token_ids, dict(vocabulary)), create_empty_token=False, show_progress=False)
    retriever.save(str(output))


def _build_faiss(documents: Sequence[HybridDocument], dense_vectors: dict[str, Sequence[float]], output: Path) -> int:
    import faiss
    import numpy as np

    if set(dense_vectors) != {document.id for document in documents}:
        raise ValueError("Dense vector IDs do not match document IDs")
    matrix = np.asarray([dense_vectors[document.id] for document in documents], dtype="float32")
    if matrix.ndim != 2 or not matrix.shape[1]:
        raise ValueError("Dense vectors must be a non-empty two-dimensional matrix")
    faiss.normalize_L2(matrix)
    index = faiss.IndexFlatIP(int(matrix.shape[1]))
    index.add(matrix)
    faiss.write_index(index, str(output))
    return int(matrix.shape[1])


def build_index(root: str | Path, corpus: str, documents: Sequence[HybridDocument], *, source_signature: str | None = None, source_hash: str | None = None, model_versions: dict[str, Any] | None = None, dropped_rows: int = 0, dense_vectors: dict[str, Sequence[float]] | None = None) -> dict[str, Any]:
    """Build in isolation, publish the complete directory, then switch CURRENT."""
    if dropped_rows < 0:
        raise ValueError("dropped_rows cannot be negative")
    ids = [document.id for document in documents]
    if not documents or len(ids) != len(set(ids)) or any(not value for value in ids):
        raise ValueError("Hybrid documents require non-empty unique deterministic IDs")
    if corpus == "columns" and dense_vectors:
        raise ValueError("The columns corpus is BM25-only")

    corpus_root = Path(root) / corpus
    builds_root = corpus_root / "builds"
    builds_root.mkdir(parents=True, exist_ok=True)
    build_id = uuid.uuid4().hex
    temporary, final = builds_root / f".tmp-{build_id}", builds_root / build_id
    temporary.mkdir(exist_ok=False)
    try:
        payload = [{"id": doc.id, "text": doc.text, "group": doc.group, "metadata": doc.metadata} for doc in documents]
        (temporary / "documents.json").write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
        _build_bm25(documents, temporary / "bm25s")
        dimension = _build_faiss(documents, dense_vectors, temporary / "faiss.index") if dense_vectors else None
        manifest = {
            "format_version": 2,
            "corpus": corpus,
            "build_id": build_id,
            "source_signature": source_signature or "",
            "source_hash": source_hash or "",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "count": len(documents),
            "dropped_rows": int(dropped_rows),
            "dense_enabled": bool(dense_vectors),
            "dense_count": len(dense_vectors or {}),
            "dimension": dimension,
            "model_versions": model_versions or {"lexical": "bm25s"},
        }
        (temporary / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        os.rename(temporary, final)
        pointer_tmp = corpus_root / f".CURRENT-{build_id}"
        pointer_tmp.write_text(build_id, encoding="utf-8")
        os.replace(pointer_tmp, corpus_root / "CURRENT")
        return manifest
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def load_index(root: str | Path, corpus: str, *, embedder: Any = None, reranker: Any = None, model_key: str = "default") -> tuple[HybridIndex, dict[str, Any]]:
    """Load a published immutable build once per process and model configuration."""
    corpus_root = Path(root) / corpus
    build_id = (corpus_root / "CURRENT").read_text(encoding="utf-8").strip()
    key = (str(corpus_root.resolve()), corpus, build_id, model_key)
    with _CACHE_LOCK:
        if key in _INDEX_CACHE:
            return _INDEX_CACHE[key]
        build_dir = corpus_root / "builds" / build_id
        manifest = json.loads((build_dir / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("format_version") != 2 or manifest.get("corpus") != corpus or manifest.get("build_id") != build_id:
            raise ValueError("Hybrid index manifest does not match CURRENT/corpus/format")
        if model_key != "default" and manifest.get("dense_enabled"):
            expected_dense = model_key.split("|", 1)[0]
            built_dense = str((manifest.get("model_versions") or {}).get("dense") or "")
            if built_dense and built_dense != expected_dense:
                raise ValueError(f"Dense model mismatch: index={built_dense!r}, runtime={expected_dense!r}")
        raw = json.loads((build_dir / "documents.json").read_text(encoding="utf-8"))
        documents = [HybridDocument(str(row["id"]), str(row["text"]), str(row.get("group") or ""), dict(row.get("metadata") or {})) for row in raw]
        if len(documents) != int(manifest.get("count", -1)):
            raise ValueError("Hybrid index document count does not match manifest")

        import bm25s
        bm25_index = bm25s.BM25.load(str(build_dir / "bm25s"), mmap=True, load_corpus=False)
        faiss_index = None
        if corpus != "columns" and manifest.get("dense_enabled"):
            import faiss
            faiss_index = faiss.read_index(str(build_dir / "faiss.index"))
            if faiss_index.ntotal != len(documents) or faiss_index.d != int(manifest.get("dimension") or -1):
                raise ValueError("FAISS index does not match manifest/documents")
        index = HybridIndex(documents, build_id=build_id, bm25_index=bm25_index, faiss_index=faiss_index, embedder=embedder if faiss_index is not None else None, reranker=reranker if corpus != "columns" else None)
        _INDEX_CACHE[key] = (index, manifest)
        return index, manifest


def clear_index_cache() -> None:
    with _CACHE_LOCK:
        _INDEX_CACHE.clear()
