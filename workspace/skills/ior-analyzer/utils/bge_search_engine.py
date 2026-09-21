"""Canonical IOR semantic search: global hybrid retrieval and session follow-up.

The global pipeline follows ``references/IOR_pipeline_search_2.py`` and keeps
FAISS + BM25 + RRF + BGE reranking available for future callers. The active
chat flow uses the same cache to build a small index over the current extract.
Heavy global assets are loaded lazily. Appeals fields and SVA logic do not
belong here.
"""
from __future__ import annotations

import logging
import os
import pickle
import re
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Iterable, List, Optional, Set

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)
_WORKSPACE_DIR = Path(__file__).resolve().parents[2]
_PIPELINES_DIR = _WORKSPACE_DIR / "data_store" / "cache" / "caches_pipelines"
_CACHE_DEFAULT = _PIPELINES_DIR / "cache_final"
_BGE_DEFAULT = _PIPELINES_DIR / "BAAI:bge-m3"
_RERANKER_DEFAULT = _PIPELINES_DIR / "bge-reranker-v2-m3"

BM25_CHUNK = 200_000
K_RRF = 60
ALPHA = 0.3
TOP_K_RERANK = 50
DEFAULT_TOP_K = 40
SCORE_THRESHOLD = 0.5
SESSION_SCORE_THRESHOLD = 0.7
MAX_CANDIDATES = 50

_LOAD_LOCK = Lock()
_MODEL_CACHE: Dict[str, Any] = {}
_SMALL_FAISS_SESSION_CACHE: Dict[str, Dict[str, Any]] = {}
_cache_loaded = False
_global_assets_loaded = False
documents: List[str] = []
doc_sids: List[str] = []
incident_ids: List[Any] = []
incident_dates: List[Any] = []
id_to_index: Dict[Any, int] = {}
sid_to_index: Dict[str, int] = {}
embeddings: Optional[np.ndarray] = None
faiss_loaded: Any = None
bm25_indexes: List[Any] = []


def cache_dir() -> Path:
    return Path(os.getenv("IOR_RAG_CACHE_DIR", str(_CACHE_DEFAULT))).expanduser()


def model_dir() -> Path:
    return Path(os.getenv("IOR_BGE_MODEL_PATH", str(_BGE_DEFAULT))).expanduser()


def reranker_dir() -> Path:
    return Path(os.getenv("IOR_RERANKER_MODEL_PATH", str(_RERANKER_DEFAULT))).expanduser()


def get_bge_model() -> Any:
    """Load BGE-M3 locally and never send IOR text to an external service."""
    if "embed" in _MODEL_CACHE:
        return _MODEL_CACHE["embed"]
    with _LOAD_LOCK:
        if "embed" in _MODEL_CACHE:
            return _MODEL_CACHE["embed"]
        path = model_dir()
        if not path.is_dir():
            raise RuntimeError(
                f"IOR BGE-M3 model is unavailable: {path}. "
                "Set IOR_BGE_MODEL_PATH or run the bootstrap notebook."
            )
        try:
            import torch
            from sentence_transformers import SentenceTransformer
            device = "cuda" if torch.cuda.is_available() else "cpu"
            model = SentenceTransformer(str(path), device=device)
        except Exception as exc:
            raise RuntimeError(f"Failed to load local IOR BGE-M3 from {path}: {exc}") from exc
        _MODEL_CACHE["embed"] = model
        return model


def get_reranker() -> Any:
    """Load the local BGE reranker only when global search needs it."""
    if "reranker" in _MODEL_CACHE:
        return _MODEL_CACHE["reranker"]
    with _LOAD_LOCK:
        if "reranker" in _MODEL_CACHE:
            return _MODEL_CACHE["reranker"]
        path = reranker_dir()
        if not path.is_dir():
            raise RuntimeError(
                f"IOR BGE reranker is unavailable: {path}. "
                "Set IOR_RERANKER_MODEL_PATH or run the bootstrap notebook."
            )
        try:
            import torch
            from sentence_transformers import CrossEncoder
            device = "cuda" if torch.cuda.is_available() else "cpu"
            model = CrossEncoder(str(path), device=device)
        except Exception as exc:
            raise RuntimeError(f"Failed to load local IOR reranker from {path}: {exc}") from exc
        _MODEL_CACHE["reranker"] = model
        return model


def _load_embeddings(root: Path) -> np.ndarray:
    meta_path = root / "embeddings_meta.pkl"
    if not meta_path.is_file():
        raise RuntimeError(f"IOR cache has no embeddings metadata: {meta_path}")
    with meta_path.open("rb") as stream:
        meta = pickle.load(stream)
    missing = {"path", "dtype", "shape"}.difference(meta)
    if missing:
        raise RuntimeError(f"IOR embeddings metadata misses fields: {sorted(missing)}")
    data_path = root / Path(str(meta["path"])).name
    if not data_path.is_file():
        raise RuntimeError(f"IOR embeddings memmap is unavailable: {data_path}")
    return np.memmap(data_path, dtype=meta["dtype"], mode="r", shape=tuple(meta["shape"]))


def load_pipeline_meta(path: Optional[os.PathLike] = None) -> None:
    """Load and validate IOR ``cache_final`` metadata and embeddings once."""
    global _cache_loaded, documents, doc_sids, incident_ids, incident_dates
    global id_to_index, sid_to_index, embeddings
    if _cache_loaded:
        return
    with _LOAD_LOCK:
        if _cache_loaded:
            return
        root = Path(path) if path is not None else cache_dir()
        meta_path = root / "meta.pkl"
        if not meta_path.is_file():
            raise RuntimeError(
                f"IOR RAG cache is unavailable: {root}. "
                "Set IOR_RAG_CACHE_DIR or run the bootstrap notebook."
            )
        with meta_path.open("rb") as stream:
            meta = pickle.load(stream)
        for field in ("documents", "doc_sids"):
            if field not in meta:
                raise RuntimeError(f"IOR cache metadata has no {field!r}")
        loaded_documents = [str(value) for value in meta["documents"]]
        loaded_sids = [str(value) for value in meta["doc_sids"]]
        if len(loaded_documents) != len(loaded_sids):
            raise RuntimeError(
                f"IOR cache integrity error: documents={len(loaded_documents)}, "
                f"doc_sids={len(loaded_sids)}"
            )
        loaded_embeddings = _load_embeddings(root)
        if len(loaded_embeddings) != len(loaded_sids):
            raise RuntimeError(
                f"IOR cache integrity error: embeddings={len(loaded_embeddings)}, "
                f"doc_sids={len(loaded_sids)}"
            )
        documents = loaded_documents
        doc_sids = loaded_sids
        incident_ids = list(meta.get("incident_ids", loaded_sids))
        incident_dates = list(meta.get("incident_dates", [None] * len(loaded_sids)))
        if len(incident_ids) != len(loaded_sids):
            incident_ids = list(loaded_sids)
        if len(incident_dates) != len(loaded_sids):
            incident_dates = [None] * len(loaded_sids)
        sid_to_index = {sid: index for index, sid in enumerate(doc_sids)}
        raw_id_map = meta.get("id_to_index") or {}
        id_to_index = {key: int(value) for key, value in raw_id_map.items()}
        id_to_index.update(sid_to_index)
        embeddings = loaded_embeddings
        _cache_loaded = True
        logger.info("Loaded IOR cache_final metadata: %s documents", len(doc_sids))


def load_global_search_assets(path: Optional[os.PathLike] = None) -> None:
    """Load the global FAISS index and BM25 shards on first explicit use."""
    global _global_assets_loaded, faiss_loaded, bm25_indexes
    if _global_assets_loaded:
        return
    root = Path(path) if path is not None else cache_dir()
    load_pipeline_meta(root)
    with _LOAD_LOCK:
        if _global_assets_loaded:
            return
        try:
            import bm25s
            import faiss

            index_path = root / "faiss_index"
            if not index_path.is_file():
                raise RuntimeError(f"IOR cache has no FAISS index: {index_path}")
            loaded_faiss = faiss.read_index(str(index_path))
            if int(loaded_faiss.ntotal) != len(doc_sids):
                raise RuntimeError(
                    f"IOR cache integrity error: FAISS ntotal={loaded_faiss.ntotal}, "
                    f"doc_sids={len(doc_sids)}"
                )
            bm25_dir = next(
                (candidate for candidate in (root / "bm25s_shards", root / "bm25s_shards2")
                 if candidate.is_dir()),
                None,
            )
            if bm25_dir is None:
                raise RuntimeError(f"IOR cache has no BM25 shard directory: {root}")
            loaded_bm25 = []
            shard_paths = sorted(
                (item for item in bm25_dir.iterdir() if item.name.startswith("shard_")),
                key=lambda item: int(item.name.split("_")[1]),
            )
            for shard in shard_paths:
                shard_id = int(shard.name.split("_")[1])
                loaded_bm25.append((bm25s.BM25.load(str(shard), load_corpus=False),
                                    shard_id * BM25_CHUNK))
            if not loaded_bm25:
                raise RuntimeError(f"IOR BM25 shard directory is empty: {bm25_dir}")
            faiss_loaded = loaded_faiss
            bm25_indexes = loaded_bm25
            _global_assets_loaded = True
            logger.info("Loaded global IOR search assets: FAISS=%s, BM25 shards=%s",
                        loaded_faiss.ntotal, len(loaded_bm25))
        except Exception:
            # A later call may retry after the closed-contour files are fixed.
            faiss_loaded = None
            bm25_indexes = []
            raise


def tokenize(text: str) -> List[str]:
    return re.findall(r"[а-яёa-z0-9]+", str(text).lower())


def build_date_mask(date_range: Optional[tuple] = None) -> Optional[np.ndarray]:
    if date_range is None:
        return None
    start_date, end_date = map(str, date_range)
    return np.asarray(
        [value is not None and start_date <= str(value) <= end_date for value in incident_dates],
        dtype=bool,
    )


def _encode_query(query: str) -> np.ndarray:
    model = get_bge_model()
    try:
        vector = model.encode(
            [query], normalize_embeddings=True, convert_to_numpy=True,
            show_progress_bar=False,
        )
    except TypeError:
        vector = model.encode([query])
    vector = np.asarray(vector, dtype="float32")
    import faiss
    faiss.normalize_L2(vector)
    return vector


def _faiss_search(index: Any, query_vector: np.ndarray, depth: int,
                  date_mask: Optional[np.ndarray]) -> np.ndarray:
    import faiss

    depth = min(depth, int(index.ntotal))
    if date_mask is None:
        return index.search(query_vector, depth)[1]
    allowed = np.ascontiguousarray(np.flatnonzero(date_mask).astype("int64"))
    if not len(allowed):
        return np.empty((1, 0), dtype="int64")
    try:
        selector = faiss.IDSelectorBatch(allowed)
        params = faiss.SearchParametersIVF()
        params.nprobe = getattr(index, "nprobe", 16)
        params.sel = selector
        return index.search(query_vector, min(depth, len(allowed)), params=params)[1]
    except (AttributeError, TypeError, RuntimeError):
        # IndexFlat and some FAISS builds do not accept SearchParametersIVF.
        raw = index.search(query_vector, int(index.ntotal))[1][0]
        allowed_set = set(map(int, allowed))
        filtered = [int(position) for position in raw if int(position) in allowed_set][:depth]
        return np.asarray([filtered], dtype="int64")


def retrieve_hybrid_adaptive(
    query: str,
    faiss_idx: Any = None,
    bm25_shards: Optional[List[Any]] = None,
    target_k: int = DEFAULT_TOP_K,
    date_range: Optional[tuple] = None,
) -> List[str]:
    """Return IOR SID candidates using masked FAISS/BM25 reciprocal-rank fusion."""
    if faiss_idx is None or bm25_shards is None:
        load_global_search_assets()
        faiss_idx = faiss_loaded if faiss_idx is None else faiss_idx
        bm25_shards = bm25_indexes if bm25_shards is None else bm25_shards
    date_mask = build_date_mask(date_range)
    faiss_depth = 2048
    bm25_total_k = int(faiss_depth * 0.67)
    bm25_k = int(np.ceil(bm25_total_k / max(1, len(bm25_shards))))

    faiss_ranks: Dict[str, int] = {}
    positions = _faiss_search(faiss_idx, _encode_query(query), faiss_depth, date_mask)
    for rank, position in enumerate(positions[0], 1):
        position = int(position)
        if 0 <= position < len(doc_sids):
            faiss_ranks.setdefault(doc_sids[position], rank)

    bm25_ranks: Dict[str, int] = {}
    query_tokens = tokenize(query)
    for bm25_index, offset in bm25_shards:
        shard_size = int(bm25_index.scores["num_docs"])
        local_mask = None if date_mask is None else date_mask[offset:offset + shard_size].astype("float32")
        allowed_count = shard_size if local_mask is None else int(local_mask.sum())
        if allowed_count == 0:
            continue
        kwargs = {"show_progress": False}
        if local_mask is not None:
            kwargs["weight_mask"] = local_mask
        results, _ = bm25_index.retrieve(
            [query_tokens], k=min(bm25_k, allowed_count), **kwargs,
        )
        for rank, local_position in enumerate(results[0], 1):
            local_position = int(local_position)
            if not 0 <= local_position < shard_size:
                continue
            if local_mask is not None and local_mask[local_position] == 0:
                continue
            global_position = offset + local_position
            if not 0 <= global_position < len(doc_sids):
                continue
            sid = doc_sids[global_position]
            bm25_ranks[sid] = min(rank, bm25_ranks.get(sid, rank))

    all_sids = set(faiss_ranks) | set(bm25_ranks)
    fused = {
        sid: ALPHA / (K_RRF + bm25_ranks.get(sid, 999))
        + (1.0 - ALPHA) / (K_RRF + faiss_ranks.get(sid, 999))
        for sid in all_sids
    }
    return [sid for sid, _ in sorted(fused.items(), key=lambda item: item[1], reverse=True)][:target_k]


def rerank_global(query: str, candidates: List[str]) -> List[Dict[str, Any]]:
    """Rerank global SID candidates and attach canonical IOR metadata."""
    valid = [sid for sid in candidates if sid in sid_to_index]
    pairs = [(query, documents[sid_to_index[sid]]) for sid in valid]
    if not pairs:
        return []
    raw_scores = np.asarray(get_reranker().predict(pairs, batch_size=32), dtype="float64")
    scores = 1.0 / (1.0 + np.exp(-raw_scores))
    results = []
    for sid, score in zip(valid, scores):
        position = sid_to_index[sid]
        results.append({
            "incident_sid": sid,
            "incident_id": incident_ids[position],
            "Текст_ИОР": documents[position],
            "date": incident_dates[position],
            "score": float(score),
        })
    return sorted(results, key=lambda item: item["score"], reverse=True)


def search_pipeline(
    query: str,
    faiss_idx: Any = None,
    bm25_shards: Optional[List[Any]] = None,
    top_k: Optional[int] = None,
    score_threshold: float = SCORE_THRESHOLD,
    date_range: Optional[tuple] = None,
) -> pd.DataFrame:
    """Run full global IOR hybrid retrieval. Kept for explicit future use."""
    target_k = top_k if top_k is not None else DEFAULT_TOP_K
    candidates_k = max(target_k * 2, TOP_K_RERANK)
    candidates = retrieve_hybrid_adaptive(
        query, faiss_idx=faiss_idx, bm25_shards=bm25_shards,
        target_k=candidates_k, date_range=date_range,
    )
    if not candidates:
        return pd.DataFrame(columns=["incident_sid", "incident_id", "Текст_ИОР", "date", "score"])
    reranked = rerank_global(query, candidates)
    selected = reranked[:top_k] if top_k is not None else [
        item for item in reranked[:target_k] if item["score"] >= score_threshold
    ]
    return pd.DataFrame(selected, columns=["incident_sid", "incident_id", "Текст_ИОР", "date", "score"])


def get_id_variations(doc_id: Any) -> Set[Any]:
    """Return variations without converting long integer IDs through float."""
    variations: Set[Any] = set()
    if doc_id is None or (isinstance(doc_id, float) and np.isnan(doc_id)):
        return variations
    value = str(doc_id).strip()
    if not value:
        return variations
    variations.add(value)
    if value.endswith(".0") and value[:-2].isdigit():
        value = value[:-2]
        variations.add(value)
    if value.isdigit():
        integer = int(value)
        variations.update((integer, str(integer), f"{integer}.0"))
    return variations


def _first_column(frame: pd.DataFrame, names: Iterable[str]) -> Optional[str]:
    by_lower = {str(column).strip().casefold(): column for column in frame.columns}
    return next((by_lower[name.casefold()] for name in names if name.casefold() in by_lower), None)


def _session_rows(frame: pd.DataFrame) -> List[Dict[str, Any]]:
    id_col = _first_column(
        frame, ("incdnt_sid", "идентификатор события", "id", "incdnt_id", "номер иор")
    )
    text_col = _first_column(
        frame,
        ("incdnt_full_descr_txt", "подробное описание", "полное описание",
         "описание события", "incdnt_summary_descr_txt", "краткое описание"),
    )
    if id_col is None or text_col is None:
        return []
    rows: List[Dict[str, Any]] = []
    for _, row in frame.iterrows():
        if pd.isna(row[id_col]) or pd.isna(row[text_col]):
            continue
        text = str(row[text_col]).strip()
        if text:
            rows.append({"id": row[id_col], "text": text})
    return rows


def _lookup_cache_index(doc_id: Any) -> Optional[int]:
    for variation in get_id_variations(doc_id):
        sid_match = sid_to_index.get(str(variation))
        if sid_match is not None:
            return sid_match
        if variation in id_to_index:
            return int(id_to_index[variation])
        if str(variation) in id_to_index:
            return int(id_to_index[str(variation)])
    return None


def build_and_cache_small_index(session_id: str, df_or_map: Any) -> bool:
    """Build a session FAISS index using matching vectors from ``cache_final``."""
    if not session_id:
        return False
    try:
        load_pipeline_meta()
    except Exception as exc:
        logger.warning("Cannot build IOR session index without cache_final: %s", exc)
        return False
    if embeddings is None:
        return False
    if isinstance(df_or_map, pd.DataFrame):
        rows = _session_rows(df_or_map)
    elif isinstance(df_or_map, dict):
        rows = [
            {"id": key, "text": value.get("text") or value.get("desc") or ""}
            if isinstance(value, dict) else {"id": key, "text": str(value)}
            for key, value in df_or_map.items()
        ]
    else:
        return False
    selected_rows: List[Dict[str, Any]] = []
    selected_indices: List[int] = []
    seen: Set[str] = set()
    for row in rows:
        index = _lookup_cache_index(row["id"])
        key = str(row["id"])
        if index is None or key in seen:
            continue
        seen.add(key)
        selected_rows.append({"id": row["id"], "text": str(row["text"])})
        selected_indices.append(index)
    if not selected_indices:
        logger.warning("No session IOR IDs matched cache_final metadata")
        return False
    try:
        import faiss
        vectors = np.asarray(embeddings[selected_indices], dtype="float32").copy()
        faiss.normalize_L2(vectors)
        index = faiss.IndexFlatIP(vectors.shape[1])
        index.add(vectors)
        previous = _SMALL_FAISS_SESSION_CACHE.get(session_id, {})
        _SMALL_FAISS_SESSION_CACHE[session_id] = {
            "index": index,
            "descriptions": selected_rows,
            "history": previous.get("history", []),
            "target_ids": [row["id"] for row in selected_rows],
        }
        logger.info("Built IOR session index %r: matched=%s, requested=%s",
                    session_id, len(selected_rows), len(rows))
        return True
    except Exception as exc:
        logger.warning("Failed to build IOR session index: %s", exc)
        return False


def search_small_index(
    session_id: str,
    query: str,
    threshold: float = SESSION_SCORE_THRESHOLD,
    max_candidates: int = MAX_CANDIDATES,
) -> List[Dict[str, Any]]:
    """Search only the IORs present in the current session extract."""
    session = _SMALL_FAISS_SESSION_CACHE.get(session_id)
    if not session or not query:
        return []
    descriptions = session.get("descriptions", [])
    index = session.get("index")
    if index is None or not descriptions:
        return []
    try:
        import faiss
        vector = get_bge_model().encode([query]).astype("float32")
        faiss.normalize_L2(vector)
        distances, indices = index.search(vector, min(max_candidates, len(descriptions)))
        return [
            {"text": descriptions[position]["text"], "id": descriptions[position]["id"],
             "score": float(score)}
            for position, score in zip(indices[0], distances[0])
            if 0 <= position < len(descriptions) and float(score) >= threshold
        ]
    except Exception as exc:
        logger.warning("Failed to search IOR session index: %s", exc)
        return []
