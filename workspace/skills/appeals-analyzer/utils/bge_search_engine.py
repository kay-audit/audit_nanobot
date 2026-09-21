"""Read-only BGE-M3/FAISS/BM25 retrieval and BGE reranking for appeals."""
from __future__ import annotations

import logging
import os
import pickle
import re
import gc
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

try:
    from .pipeline_config import CONFIG
except ImportError:  # direct script-style imports in Nanobot
    from pipeline_config import CONFIG

logger = logging.getLogger(__name__)
_SKILL_DIR = Path(__file__).resolve().parents[1]
_PIPELINES_DIR = _SKILL_DIR.parents[1] / "data_store" / "cache" / "caches_pipelines"
_CACHE_DEFAULT = _PIPELINES_DIR / "cache_le_finale2"
_BGE_DEFAULT = _PIPELINES_DIR / "BAAI:bge-m3"
_RERANKER_DEFAULT = _PIPELINES_DIR / "bge-reranker-v2-m3"
_BGE_CACHE: Dict[str, Any] = {}
_MODEL_LOAD_LOCK = Lock()
_SMALL_FAISS_SESSION_CACHE: Dict[str, Dict[str, Any]] = {}
_loaded = False
doc_ids: List[Any] = []
req_reg_dates: List[Any] = []
id_to_positions: Dict[str, List[int]] = {}
faiss_loaded: Any = None
bm25_indexes: List[Tuple[Any, int]] = []


def normalize_id(value: Any) -> str:
    return str(value).strip()


def cache_dir() -> Path:
    return Path(os.environ.get("APPEALS_RAG_CACHE_DIR", str(_CACHE_DEFAULT)))


def resolve_rag_metadata_path(root: Path) -> Path:
    explicit = os.environ.get("APPEALS_RAG_META_FILE")
    if explicit:
        selected = Path(explicit)
        if not selected.is_absolute():
            selected = root / selected
        if not selected.is_file():
            raise RuntimeError(f"Configured RAG metadata is unavailable: {selected}")
        return selected
    primary = root / "meta.pkl"
    fallback = root / "meta_final.pkl"
    if primary.is_file():
        return primary
    if fallback.is_file():
        return fallback
    raise RuntimeError(f"RAG metadata is unavailable in {root}: expected meta.pkl or meta_final.pkl.")


def _first_existing_model_path(env_name: str, candidates: Sequence[str]) -> Optional[Path]:
    explicit = os.environ.get(env_name)
    paths = ([explicit] if explicit else []) + list(candidates)
    return next((Path(value) for value in paths if value and Path(value).exists()), None)


def _parse_device_spec(explicit: str, count: int, env_name: str) -> List[str]:
    devices: List[str] = []
    for raw in explicit.split(","):
        value = raw.strip().casefold()
        if not value:
            continue
        if value == "cpu":
            device = "cpu"
        else:
            index_text = value.removeprefix("cuda:")
            if not index_text.isdigit() or int(index_text) >= count:
                raise ValueError(f"Invalid {env_name} entry '{raw}' for {count} visible GPU(s).")
            device = f"cuda:{int(index_text)}"
        if device not in devices:
            devices.append(device)
    if not devices:
        raise ValueError(f"{env_name} did not contain any valid device.")
    return devices


def resolve_compute_devices(torch_module: Any = None) -> List[str]:
    """Return logical CUDA devices, honoring APPEALS_CUDA_DEVICES=0,1."""
    torch_module = torch_module or __import__("torch")
    cuda = getattr(torch_module, "cuda", None)
    if cuda is None or not cuda.is_available():
        return ["cpu"]
    count = int(getattr(cuda, "device_count", lambda: 1)())
    explicit = os.getenv("APPEALS_CUDA_DEVICES", "").strip()
    if not explicit:
        return [f"cuda:{index}" for index in range(max(1, count))]
    return _parse_device_spec(explicit, count, "APPEALS_CUDA_DEVICES")


def resolve_model_devices(model_kind: str, torch_module: Any = None) -> List[str]:
    """Resolve safe per-model placement while preserving the legacy device order."""
    torch_module = torch_module or __import__("torch")
    base_devices = resolve_compute_devices(torch_module)
    env_name = "APPEALS_EMBED_DEVICES" if model_kind == "embed" else "APPEALS_RERANK_DEVICES"
    explicit = os.getenv(env_name, "").strip()
    if explicit:
        cuda = getattr(torch_module, "cuda", None)
        if cuda is None or not cuda.is_available():
            return ["cpu"]
        count = int(getattr(cuda, "device_count", lambda: 1)())
        return _parse_device_spec(explicit, count, env_name)
    cuda_devices = [device for device in base_devices if device.startswith("cuda:")]
    if not cuda_devices:
        return ["cpu"]
    if model_kind == "embed":
        return [cuda_devices[0]]
    return [cuda_devices[1] if len(cuda_devices) > 1 else cuda_devices[0]]


def _env_flag(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().casefold() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, str(default))))
    except ValueError:
        logger.warning("Invalid %s; using %.2f", name, default)
        return default


def _cuda_has_headroom(device: str, required_model_gb: float = 0.0, torch_module: Any = None) -> bool:
    if not device.startswith("cuda:"):
        return True
    torch_module = torch_module or __import__("torch")
    cuda = getattr(torch_module, "cuda", None)
    mem_get_info = getattr(cuda, "mem_get_info", None)
    if mem_get_info is None:
        logger.warning("CUDA free-memory admission check is unavailable for %s; continuing without it.", device)
        return True
    try:
        free_bytes, total_bytes = mem_get_info(device)
    except (TypeError, RuntimeError):
        free_bytes, total_bytes = mem_get_info(int(device.split(":", 1)[1]))
    reserve_gb = _env_float("APPEALS_MIN_FREE_VRAM_GB", 4.0)
    required_gb = required_model_gb + reserve_gb
    free_gb = float(free_bytes) / (1024 ** 3)
    logger.info(
        "VRAM admission %s: free=%.2fGiB total=%.2fGiB required=%.2fGiB (model=%.2f reserve=%.2f)",
        device, free_gb, float(total_bytes) / (1024 ** 3), required_gb, required_model_gb, reserve_gb,
    )
    return free_gb >= required_gb


def _is_cuda_oom(exc: BaseException) -> bool:
    try:
        torch_module = __import__("torch")
        oom_type = getattr(getattr(torch_module, "cuda", None), "OutOfMemoryError", None)
        if oom_type is not None and isinstance(exc, oom_type):
            return True
    except Exception:
        pass
    message = str(exc).casefold()
    return "cuda" in message and ("out of memory" in message or "oom" in message)


def _release_cuda_cache() -> None:
    gc.collect()
    try:
        torch_module = __import__("torch")
        empty_cache = getattr(getattr(torch_module, "cuda", None), "empty_cache", None)
        if empty_cache is not None:
            empty_cache()
    except Exception:
        pass


def _model_kwargs(device: str) -> Dict[str, Any]:
    if not device.startswith("cuda:"):
        return {}
    dtype_name = os.getenv("APPEALS_MODEL_DTYPE", "float16").strip().casefold()
    if dtype_name not in {"float16", "bfloat16", "float32"}:
        raise ValueError("APPEALS_MODEL_DTYPE must be float16, bfloat16 or float32.")
    torch_module = __import__("torch")
    return {"model_kwargs": {"torch_dtype": getattr(torch_module, dtype_name)}}


def _load_transformer(model_class: Any, path: Path, device: str) -> Any:
    kwargs = _model_kwargs(device)
    try:
        return model_class(str(path), device=device, **kwargs)
    except TypeError as exc:
        if not kwargs or "model_kwargs" not in str(exc):
            raise
        logger.warning("Installed sentence-transformers does not accept model_kwargs; applying dtype after load.")
        model = model_class(str(path), device=device)
        dtype_name = os.getenv("APPEALS_MODEL_DTYPE", "float16").strip().casefold()
        if device.startswith("cuda:") and dtype_name == "float16":
            target = getattr(model, "model", model)
            half = getattr(target, "half", None)
            if half is not None:
                half()
        return model


def _load_cpu_fallback(model_kind: str) -> Any:
    if not _env_flag("APPEALS_CPU_FALLBACK", True):
        return None
    key = f"{model_kind}_cpu"
    with _MODEL_LOAD_LOCK:
        if key in _BGE_CACHE:
            return _BGE_CACHE[key]
        path = _BGE_CACHE.get(f"{model_kind}_path")
        if path is None:
            return None
        from sentence_transformers import CrossEncoder, SentenceTransformer
        model_class = SentenceTransformer if model_kind == "embed" else CrossEncoder
        logger.warning("Loading %s CPU fallback model.", model_kind)
        model = _load_transformer(model_class, path, "cpu")
        _BGE_CACHE[key] = model
        return model


def _balanced_ranges(length: int, workers: int) -> List[Tuple[int, int]]:
    workers = min(max(1, workers), length)
    base, remainder = divmod(length, workers)
    ranges = []
    start = 0
    for index in range(workers):
        end = start + base + (1 if index < remainder else 0)
        ranges.append((start, end))
        start = end
    return ranges


class MultiDeviceSentenceTransformer:
    """Run large embedding batches concurrently on independent GPU replicas."""

    def __init__(self, models: Sequence[Any], devices: Sequence[str]) -> None:
        self.models = list(models)
        self.devices = list(devices)

    def encode(self, sentences: Any, **kwargs: Any) -> Any:
        if isinstance(sentences, str) or len(sentences) < 2 or len(self.models) == 1:
            return self.models[0].encode(sentences, **kwargs)
        ranges = _balanced_ranges(len(sentences), len(self.models))
        logger.info("Embedding batch split across %s: items=%s shards=%s", self.devices[:len(ranges)], len(sentences), ranges)
        with ThreadPoolExecutor(max_workers=len(ranges), thread_name_prefix="appeals-embed-gpu") as executor:
            futures = [
                executor.submit(self.models[index].encode, sentences[start:end], **kwargs)
                for index, (start, end) in enumerate(ranges)
            ]
            parts = [np.asarray(future.result()) for future in futures]
        return np.concatenate(parts, axis=0)


class SafeSentenceTransformer:
    """Use GPU embeddings when safe and transparently fall back to CPU on pressure/OOM."""

    def __init__(self, primary: MultiDeviceSentenceTransformer) -> None:
        self.primary = primary
        self.devices = list(primary.devices)

    def encode(self, sentences: Any, **kwargs: Any) -> Any:
        gpu_devices = [device for device in self.devices if device.startswith("cuda:")]
        if gpu_devices and not all(_cuda_has_headroom(device) for device in gpu_devices):
            cpu = _load_cpu_fallback("embed")
            if cpu is None:
                raise RuntimeError("Insufficient free VRAM for BGE-M3 and CPU fallback is disabled.")
            logger.warning("BGE-M3 runtime VRAM reserve reached; using CPU fallback.")
            return cpu.encode(sentences, **kwargs)
        batch_size = int(kwargs.pop("batch_size", CONFIG.embedding_batch_size))
        while True:
            try:
                return self.primary.encode(sentences, batch_size=batch_size, **kwargs)
            except Exception as exc:
                if not _is_cuda_oom(exc):
                    raise
                _release_cuda_cache()
                if batch_size > 1:
                    batch_size = max(1, batch_size // 2)
                    logger.warning("BGE-M3 CUDA OOM; retrying with batch_size=%s", batch_size)
                    continue
                cpu = _load_cpu_fallback("embed")
                if cpu is None:
                    raise
                logger.warning("BGE-M3 CUDA OOM at batch_size=1; using CPU fallback.")
                return cpu.encode(sentences, batch_size=CONFIG.embedding_batch_size, **kwargs)


class MultiDeviceCrossEncoder:
    """Run reranker shards concurrently and preserve original pair order."""

    def __init__(self, models: Sequence[Any], devices: Sequence[str]) -> None:
        self.models = list(models)
        self.devices = list(devices)

    def predict(self, pairs: Sequence[Any], **kwargs: Any) -> np.ndarray:
        if len(pairs) < 2 or len(self.models) == 1:
            return np.asarray(self.models[0].predict(pairs, **kwargs))
        ranges = _balanced_ranges(len(pairs), len(self.models))
        logger.info("Reranker batch split across %s: pairs=%s shards=%s", self.devices[:len(ranges)], len(pairs), ranges)
        with ThreadPoolExecutor(max_workers=len(ranges), thread_name_prefix="appeals-rerank-gpu") as executor:
            futures = [
                executor.submit(self.models[index].predict, pairs[start:end], **kwargs)
                for index, (start, end) in enumerate(ranges)
            ]
            parts = [np.asarray(future.result()) for future in futures]
        return np.concatenate(parts, axis=0)


def get_bge_models():
    if "embed" in _BGE_CACHE:
        return _BGE_CACHE["embed"], _BGE_CACHE["reranker"]
    embed = reranker = None
    try:
        from sentence_transformers import CrossEncoder, SentenceTransformer
        embed_path = _first_existing_model_path("APPEALS_BGE_MODEL_PATH", (
            str(_BGE_DEFAULT),
        ))
        rerank_path = _first_existing_model_path("APPEALS_RERANKER_MODEL_PATH", (
            str(_RERANKER_DEFAULT),
        ))
        embed_devices = resolve_model_devices("embed")
        reranker_devices = resolve_model_devices("reranker")
        _BGE_CACHE.update(embed_path=embed_path, reranker_path=rerank_path)
        logger.info("BGE placement: embed=%s reranker=%s", embed_devices, reranker_devices)
        if embed_path is not None:
            embed_models, loaded_embed_devices = [], []
            estimated_gb = _env_float("APPEALS_EMBED_VRAM_GB", 2.5)
            for device in embed_devices:
                if not _cuda_has_headroom(device, estimated_gb):
                    logger.warning("Skipping BGE-M3 on %s: VRAM safety reserve would be violated.", device)
                    continue
                try:
                    embed_models.append(_load_transformer(SentenceTransformer, embed_path, device))
                    loaded_embed_devices.append(device)
                except Exception as exc:
                    logger.warning("BGE-M3 failed to load on %s: %s", device, exc)
            if embed_models:
                embed = SafeSentenceTransformer(MultiDeviceSentenceTransformer(embed_models, loaded_embed_devices))
            elif _env_flag("APPEALS_CPU_FALLBACK", True):
                embed = _load_cpu_fallback("embed")
        else:
            logger.warning("No local BGE-M3 path found; remote model loading is disabled.")
        if rerank_path is not None:
            reranker_models, loaded_reranker_devices = [], []
            estimated_gb = _env_float("APPEALS_RERANK_VRAM_GB", 2.5)
            for device in reranker_devices:
                if not _cuda_has_headroom(device, estimated_gb):
                    logger.warning("Skipping BGE reranker on %s: VRAM safety reserve would be violated.", device)
                    continue
                try:
                    reranker_models.append(_load_transformer(CrossEncoder, rerank_path, device))
                    loaded_reranker_devices.append(device)
                except Exception as exc:
                    logger.warning("BGE reranker failed to load on %s: %s", device, exc)
            if reranker_models:
                reranker = MultiDeviceCrossEncoder(reranker_models, loaded_reranker_devices)
            elif _env_flag("APPEALS_CPU_FALLBACK", True):
                reranker = _load_cpu_fallback("reranker")
        else:
            logger.warning("No local BGE reranker path found; remote model loading is disabled.")
        _BGE_CACHE["embed_devices"] = embed_devices
        _BGE_CACHE["reranker_devices"] = reranker_devices
    except Exception as exc:
        logger.warning("BGE models unavailable: %s", exc)
    _BGE_CACHE.update(embed=embed, reranker=reranker)
    return embed, reranker


def validate_cache_metadata(ids: Sequence[Any], dates: Sequence[Any], faiss_index: Any) -> None:
    if not ids:
        raise RuntimeError("RAG cache integrity error: metadata contains no doc_ids.")
    if len(dates) != len(ids):
        raise RuntimeError(f"RAG cache integrity error: req_reg_dates={len(dates)}, doc_ids={len(ids)}.")
    if int(faiss_index.ntotal) != len(ids):
        raise RuntimeError(f"RAG cache integrity error: FAISS ntotal={faiss_index.ntotal}, doc_ids={len(ids)}.")


def load_pipeline_meta_and_indices(path: Optional[os.PathLike] = None) -> None:
    global _loaded, doc_ids, req_reg_dates, id_to_positions, faiss_loaded, bm25_indexes
    if _loaded:
        return
    root = Path(path) if path else cache_dir()
    if not root.is_dir():
        raise RuntimeError(f"RAG cache is unavailable: {root}. Set APPEALS_RAG_CACHE_DIR on the closed contour.")
    try:
        import bm25s
        import faiss
        meta_path = resolve_rag_metadata_path(root)
        logger.info("Using RAG metadata: %s", meta_path)
        with meta_path.open("rb") as handle:
            meta = pickle.load(handle)
        doc_ids = list(meta.get("doc_ids", []))
        raw_dates = meta.get("req_reg_dates")
        req_reg_dates = [None] * len(doc_ids) if raw_dates is None else list(raw_dates)
        id_to_positions = {}
        for pos, doc_id in enumerate(doc_ids):
            id_to_positions.setdefault(normalize_id(doc_id), []).append(pos)
        collisions = {key: positions for key, positions in id_to_positions.items() if len(positions) > 1}
        if collisions:
            logger.warning("Normalized cache ID collisions: %s keys", len(collisions))
        faiss_loaded = faiss.read_index(str(root / "faiss_index"))
        validate_cache_metadata(doc_ids, req_reg_dates, faiss_loaded)
        bm_dir = next((root / name for name in ("bm25s_shards2", "bm25s_shards") if (root / name).is_dir()), None)
        if bm_dir is None:
            raise RuntimeError("No BM25 shard directory (bm25s_shards2/bm25s_shards) in RAG cache.")
        logger.info("Using BM25 shards: %s", bm_dir)
        bm25_indexes = []
        shards = sorted((p for p in bm_dir.iterdir() if p.name.startswith("shard_")), key=lambda p: p.name)
        if not shards:
            raise RuntimeError("RAG cache integrity error: BM25 shard directory is empty.")
        seen_shards = set()
        for shard in shards:
            match = re.fullmatch(r"shard_(\d+)", shard.name)
            if not match:
                raise RuntimeError(f"RAG cache integrity error: invalid BM25 shard name '{shard.name}'.")
            shard_id = int(match.group(1))
            if shard_id in seen_shards:
                raise RuntimeError(f"RAG cache integrity error: duplicate BM25 shard id {shard_id}.")
            seen_shards.add(shard_id)
            offset = shard_id * CONFIG.bm25_chunk
            if offset >= len(doc_ids):
                raise RuntimeError(f"RAG cache integrity error: BM25 shard {shard_id} offset {offset} exceeds metadata.")
            index = bm25s.BM25.load(str(shard), load_corpus=False)
            size = int(index.scores["num_docs"])
            if size <= 0 or offset + size > len(doc_ids):
                raise RuntimeError(
                    f"RAG cache integrity error: BM25 shard {shard_id} covers [{offset}, {offset + size}) "
                    f"outside doc_ids length {len(doc_ids)}."
                )
            bm25_indexes.append((index, offset))
        coverage = sorted((offset, offset + int(index.scores["num_docs"])) for index, offset in bm25_indexes)
        expected_start = 0
        for start, end in coverage:
            if start != expected_start:
                raise RuntimeError(
                    f"RAG cache integrity error: BM25 coverage gap/overlap at {expected_start}, next shard starts {start}."
                )
            expected_start = end
        if expected_start != len(doc_ids):
            raise RuntimeError(
                f"RAG cache integrity error: BM25 covers {expected_start} positions, doc_ids has {len(doc_ids)}."
            )
        _loaded = True
    except Exception:
        # Do not cache a failed external-contour load; test fixtures can retry.
        raise


def build_allowed_mask(
    allowed_candidate_ids: Optional[Sequence[Any]] = None,
    date_range: Optional[Tuple[Optional[str], Optional[str]]] = None,
) -> Optional[np.ndarray]:
    """Build one global mask shared by both retrieval engines; None means unrestricted."""
    structural_mask = None
    if allowed_candidate_ids is not None:
        # allowed_candidate_ids — уже union ID из OR-групп prd/s_prd/chnl.
        structural_mask = np.zeros(len(doc_ids), dtype=bool)
        missing = 0
        for candidate_id in dict.fromkeys(normalize_id(v) for v in allowed_candidate_ids):
            positions = id_to_positions.get(candidate_id)
            if positions:
                structural_mask[positions] = True
            else:
                missing += 1
        logger.info("Structural OR IDs in cache=%s missing=%s", int(structural_mask.sum()), missing)
    date_mask = None
    if date_range is not None:
        start, end = date_range
        date_mask = np.asarray([
            isinstance(value, str)
            and (start is None or value >= start)
            and (end is None or value <= end)
            for value in req_reg_dates
        ], dtype=bool)
    if structural_mask is None:
        result = date_mask
    else:
        result = structural_mask if date_mask is None else structural_mask & date_mask
    logger.info(
        "Cache allowed positions after product/date guard=%s date_range=%s",
        None if result is None else int(result.sum()), date_range,
    )
    return result


def tokenize(text: str) -> List[str]:
    return re.findall(r"[а-яёa-z0-9]+", (text or "").lower())


def _record_best_rank(rank_map: Dict[str, int], position: int, rank: int) -> None:
    cid = normalize_id(doc_ids[position])
    previous = rank_map.get(cid)
    if cid and (previous is None or rank < previous):
        rank_map[cid] = rank


def fuse_rrf_rank_maps(faiss_ranks: Dict[str, int], bm25_ranks: Dict[str, int]) -> List[str]:
    all_ids = set(faiss_ranks) | set(bm25_ranks)
    fused = {
        cid: CONFIG.rrf_alpha / (CONFIG.rrf_k + bm25_ranks.get(cid, 999))
        + (1 - CONFIG.rrf_alpha) / (CONFIG.rrf_k + faiss_ranks.get(cid, 999))
        for cid in all_ids
    }
    return [cid for cid, _ in sorted(fused.items(), key=lambda item: item[1], reverse=True)]


def retrieve_hybrid_adaptive(
    query: str,
    allowed_candidate_ids: Optional[Sequence[Any]] = None,
    date_range: Optional[Tuple[Optional[str], Optional[str]]] = None,
) -> List[Any]:
    load_pipeline_meta_and_indices()
    embed, _ = get_bge_models()
    if faiss_loaded is None or embed is None:
        raise RuntimeError("BGE embedding model or FAISS index is unavailable; semantic retrieval cannot continue.")
    allowed_mask = build_allowed_mask(allowed_candidate_ids, date_range)
    if allowed_mask is not None and not allowed_mask.any():
        logger.info("Combined allowed mask is empty.")
        return []
    import faiss
    vector = embed.encode([query], normalize_embeddings=True, convert_to_numpy=True).astype("float32")
    faiss_k = min(CONFIG.faiss_k, int(allowed_mask.sum()) if allowed_mask is not None else faiss_loaded.ntotal)
    if allowed_mask is None:
        _, found = faiss_loaded.search(vector, faiss_k)
    else:
        selected = np.ascontiguousarray(np.flatnonzero(allowed_mask).astype("int64"))
        params = faiss.SearchParametersIVF(); params.nprobe = getattr(faiss_loaded, "nprobe", 16); params.sel = faiss.IDSelectorBatch(selected)
        _, found = faiss_loaded.search(vector, faiss_k, params=params)
    faiss_ranks: Dict[str, int] = {}
    for rank, idx in enumerate(found[0], 1):
        position = int(idx)
        if 0 <= position < len(doc_ids):
            _record_best_rank(faiss_ranks, position, rank)
    bm25_ranks: Dict[str, int] = {}
    per_shard = int(np.ceil(CONFIG.bm25_total_k / max(1, len(bm25_indexes))))
    for index, offset in bm25_indexes:
        size = int(index.scores["num_docs"])
        local_mask = None if allowed_mask is None else allowed_mask[offset:offset + size].astype("float32")
        allowed = size if local_mask is None else int(local_mask.sum())
        if not allowed:
            continue
        kwargs = {"show_progress": False}
        if local_mask is not None: kwargs["weight_mask"] = local_mask
        results, _ = index.retrieve([tokenize(query)], k=min(per_shard, allowed), **kwargs)
        for rank, local_id in enumerate(results[0], 1):
            global_pos = offset + int(local_id)
            if 0 <= global_pos < len(doc_ids) and (local_mask is None or local_mask[int(local_id)]):
                _record_best_rank(bm25_ranks, global_pos, rank)
    logger.info("Hybrid retrieval: allowed=%s FAISS=%s BM25=%s", None if allowed_mask is None else int(allowed_mask.sum()), len(faiss_ranks), len(bm25_ranks))
    return fuse_rrf_rank_maps(faiss_ranks, bm25_ranks)


def build_text(req_desc: Any, msg_pprb_chat: Any) -> str:
    return " ".join(str(v).strip() for v in (req_desc, msg_pprb_chat) if pd.notna(v) and str(v).strip())


def rerank_dataframe(query: str, df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    _, model = get_bge_models()
    if model is None:
        raise RuntimeError("BGE-Reranker-v2-M3 is unavailable; candidates must not be accepted without scores.")
    pairs = [(query, build_text(row.get("Короткое описание", row.get("short_description", "")), row.get("msg_pprb_chat", row.get("Транскрибация диалога", row.get("description", ""))))) for _, row in df.iterrows()]
    gpu_devices = [device for device in getattr(model, "devices", []) if device.startswith("cuda:")]
    if gpu_devices and not all(_cuda_has_headroom(device) for device in gpu_devices):
        fallback = _load_cpu_fallback("reranker")
        if fallback is None:
            raise RuntimeError("Insufficient free VRAM for BGE reranker and CPU fallback is disabled.")
        logger.warning("Reranker runtime VRAM reserve reached; using CPU fallback.")
        model = fallback
    batch_size = CONFIG.reranker_batch_size
    while True:
        try:
            raw = np.asarray(model.predict(pairs, batch_size=batch_size))
            break
        except Exception as exc:
            if not _is_cuda_oom(exc):
                raise RuntimeError("BGE reranker failed; no semantic result was produced.") from exc
            _release_cuda_cache()
            if batch_size > 1:
                batch_size = max(1, batch_size // 2)
                logger.warning("BGE reranker CUDA OOM; retrying with batch_size=%s", batch_size)
                continue
            fallback = _load_cpu_fallback("reranker")
            if fallback is None or fallback is model:
                raise RuntimeError("BGE reranker exhausted CUDA memory and CPU fallback is unavailable.") from exc
            logger.warning("BGE reranker CUDA OOM at batch_size=1; using CPU fallback.")
            model = fallback
            batch_size = CONFIG.reranker_batch_size
    result = df.copy(); result["score"] = 1 / (1 + np.exp(-raw))
    return result.sort_values("score", ascending=False, kind="stable").reset_index(drop=True)


def select_threshold_or_fallback(scored: pd.DataFrame) -> Tuple[pd.DataFrame, bool]:
    if scored.empty or "score" not in scored:
        raise RuntimeError("Reranker produced no scores.")
    accepted = scored[scored["score"] >= CONFIG.score_threshold].copy()
    return (accepted, False) if not accepted.empty else (scored.head(CONFIG.fallback_top_k).copy(), True)


def build_and_cache_small_index(session_id: str, df_or_map: Any, **_: Any) -> bool:
    rows = df_or_map.to_dict("records") if isinstance(df_or_map, pd.DataFrame) else list((df_or_map or {}).values())
    documents = [build_text(row.get("desc", row.get("Короткое описание", "")), row.get("dialogue", row.get("Транскрибация диалога", row.get("msg_pprb_chat", "")))) for row in rows]
    valid = [(row, text) for row, text in zip(rows, documents) if text]
    if not valid:
        _SMALL_FAISS_SESSION_CACHE[session_id] = {"index": None, "rows": [], "documents": [], "mode": "empty"}
        return False
    rows, documents = map(list, zip(*valid))
    embed, _ = get_bge_models()
    if embed is None:
        logger.warning("Session FAISS unavailable: local BGE-M3 model is missing; lexical degraded fallback enabled.")
        _SMALL_FAISS_SESSION_CACHE[session_id] = {"index": None, "rows": rows, "documents": documents, "mode": "lexical_fallback"}
        return True
    try:
        import faiss
        embeddings = np.asarray(embed.encode(documents, normalize_embeddings=True, convert_to_numpy=True), dtype="float32")
        faiss.normalize_L2(embeddings)
        index = faiss.IndexFlatIP(embeddings.shape[1])
        index.add(embeddings)
        _SMALL_FAISS_SESSION_CACHE[session_id] = {
            "index": index, "rows": rows, "documents": documents, "embeddings": embeddings, "mode": "faiss",
        }
        return True
    except Exception as exc:
        logger.warning("Session FAISS build failed; lexical degraded fallback enabled: %s", exc)
        _SMALL_FAISS_SESSION_CACHE[session_id] = {"index": None, "rows": rows, "documents": documents, "mode": "lexical_fallback"}
        return True


def clear_small_index(session_id: str) -> None:
    """Invalidate any prior follow-up index for a session."""
    _SMALL_FAISS_SESSION_CACHE.pop(session_id, None)


def search_small_index(session_id: str, query: str, max_candidates: int = 50, **_: Any) -> List[Dict[str, Any]]:
    state = _SMALL_FAISS_SESSION_CACHE.get(session_id, {})
    rows = state.get("rows", [])
    index = state.get("index")
    if not rows or not query:
        return []
    embed, _ = get_bge_models()
    if index is not None and embed is not None:
        import faiss
        query_embedding = np.asarray(embed.encode([query], normalize_embeddings=True, convert_to_numpy=True), dtype="float32")
        faiss.normalize_L2(query_embedding)
        scores, indices = index.search(query_embedding, min(max_candidates, len(rows)))
        candidates = []
        for score, position in zip(scores[0], indices[0]):
            if 0 <= int(position) < len(rows):
                candidate = dict(rows[int(position)])
                candidate["_similarity_score"] = float(score)
                candidate["_candidate_mode"] = "semantic"
                candidates.append(candidate)
        return candidates
    logger.warning("Session search uses degraded lexical fallback for session '%s'.", session_id)
    tokens = set(tokenize(query))
    ranked = sorted(rows, key=lambda row: len(tokens & set(tokenize(str(row)))), reverse=True)
    candidates = []
    for row in ranked:
        if not tokens & set(tokenize(str(row))):
            continue
        candidate = dict(row)
        candidate["_similarity_score"] = None
        candidate["_candidate_mode"] = "lexical_degraded"
        candidates.append(candidate)
        if len(candidates) >= max_candidates:
            break
    return candidates
