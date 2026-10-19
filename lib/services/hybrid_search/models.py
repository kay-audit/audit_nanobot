"""Lazy optional BGE-M3 and cross-encoder adapters."""
from __future__ import annotations

import threading
from typing import Callable, Sequence

_LOCK = threading.Lock()
_MODELS: dict[tuple[str, str, str, str], object] = {}


class ModelUnavailableError(RuntimeError):
    code = "model_unavailable"


def _device(value: str) -> str:
    if value != "auto":
        return value
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def make_bge_embedder(model_name: str = "BAAI/bge-m3", *, device: str = "cpu", cache_dir: str | None = None) -> Callable[[list[str]], Sequence[Sequence[float]]]:
    def embed(texts: list[str]):
        resolved = _device(device); key = ("dense", model_name, resolved, cache_dir or "")
        try:
            with _LOCK:
                model = _MODELS.get(key)
                if model is None:
                    from sentence_transformers import SentenceTransformer
                    model = SentenceTransformer(model_name, device=resolved, cache_folder=cache_dir, local_files_only=True)
                    _MODELS[key] = model
            return model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        except Exception as exc:
            raise ModelUnavailableError(f"Dense model {model_name!r} is unavailable on {resolved}: {exc}") from exc
    return embed


def make_bge_reranker(model_name: str = "BAAI/bge-reranker-v2-m3", *, device: str = "cpu", cache_dir: str | None = None) -> Callable[[str, list[str]], Sequence[float]]:
    def rerank(query: str, documents: list[str]):
        resolved = _device(device); key = ("reranker", model_name, resolved, cache_dir or "")
        try:
            with _LOCK:
                model = _MODELS.get(key)
                if model is None:
                    from sentence_transformers import CrossEncoder
                    model = CrossEncoder(model_name, device=resolved, cache_folder=cache_dir, local_files_only=True)
                    _MODELS[key] = model
            values = model.predict([(query, text) for text in documents])
            return [float(value) for value in values]
        except Exception as exc:
            raise ModelUnavailableError(f"Reranker model {model_name!r} is unavailable on {resolved}: {exc}") from exc
    return rerank
