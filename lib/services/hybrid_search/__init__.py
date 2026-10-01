"""Lightweight runtime surface for SQL Assistant hybrid retrieval.

Dense embeddings/reranking are optional adapters.  The deterministic lexical
path remains usable in minimal gateway and unit-test environments.
"""
from .core import HybridDocument, HybridIndex, SearchHit, SearchOutcome, prepare_from_frame
from .storage import build_index, clear_index_cache, load_index
from .models import ModelUnavailableError, make_bge_embedder, make_bge_reranker

__all__ = ["HybridDocument", "HybridIndex", "SearchHit", "SearchOutcome", "prepare_from_frame", "build_index", "load_index", "clear_index_cache", "ModelUnavailableError", "make_bge_embedder", "make_bge_reranker"]
