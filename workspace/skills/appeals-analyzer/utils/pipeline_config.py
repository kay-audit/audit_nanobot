"""Single tuning point for the legacy-style appeals retrieval pipeline."""
from dataclasses import dataclass


@dataclass(frozen=True)
class PipelineConfig:
    faiss_k: int = 2500
    bm25_total_k: int = 2000
    rrf_k: int = 60
    rrf_alpha: float = 0.3
    score_threshold: float = 0.5
    fallback_top_k: int = 2048
    reranker_batch_size: int = 8
    embedding_batch_size: int = 8
    bm25_chunk: int = 1_000_000
    followup_chunk_chars: int = 900
    hypothesis_sample_size: int = 200
    hypothesis_batch_size: int = 20
    hypothesis_chars_per_appeal: int = 400
    hypothesis_batch_char_budget: int = 12_000
    hypothesis_reduce_char_budget: int = 45_000
    hypothesis_profile_max_columns: int = 15
    hypothesis_profile_top_values: int = 12


CONFIG = PipelineConfig()
