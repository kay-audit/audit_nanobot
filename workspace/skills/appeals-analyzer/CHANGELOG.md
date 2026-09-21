# Changelog

## Single-job hydration

- Full RRF hydration now runs BASE, DIALOGS and TASKS on one shared connection in one `db.run()` job.
- Removed hydration chunking; merge and normalization run only after the shared connection is released.

## Shared Greenplum pool

- Appeals SQL jobs now use the gateway-wide `workspace/utils/db.py` queue and worker pool.
- Removed the skill-owned connection, DSN fallback and connect retry lifecycle.
- Standalone CLI configures and starts the same shared DB runtime, then stops it only when the CLI started it.

## Isolated SVA classification and bounded hypothesis evidence

- SVA classification is isolated in `utils/sva_metrics.py` and disabled in the active appeals pipeline.
- Hypothesis evidence is capped at 200 deterministic stratified appeals, in batches of at most 20.
- Each appeal contributes at most 400 text characters to evidence payloads; full exports and session data remain unchanged.

## Clean profile and detailed hypotheses

- Removed `Greenplum` from the user-facing mathematical profile heading.
- Monthly dynamics now displays the calendar month end as `YYYY-MM-DD`.
- Replaced related-task statuses with the Top-5 `grp` distribution.
- Removed raw short-description frequency dumps and dialogue excerpts from the profile.
- The deterministic profile is now composed directly by code; the LLM generates exactly four detailed hypotheses in plain business language.

## Safe GPU placement and fixed hypothesis evidence

- BGE-M3 and reranker no longer replicate onto every visible GPU by default: embedding uses the first GPU and reranking the second.
- Added FP16 loading, configurable VRAM admission reserves, adaptive batches and lazy CPU fallback on memory pressure/OOM.
- Quantitative report metrics always use the complete final dataset; hypothesis evidence is capped at 300 deterministic stratified appeals.
- Final output always requires report and hypothesis sections and omits internal sampling/uncertainty disclosures.

## Current architecture

- Analytical requests use a four-field protocol with exact product/subproduct/channel values.
- Greenplum is limited to structural population IDs and full hydration of RRF candidates.
- Semantic relevance is determined by masked BGE-M3 FAISS + BM25, RRF and BGE reranking.
- Score threshold, final-only Excel/CSV, grounded evidence batching and session FAISS operate on unique final appeals.
- Cache and models are loaded lazily from existing local closed-contour paths only.

Historical entries for the superseded lexical candidate pipeline, decision bands and quality gates were removed to avoid describing them as active behavior.
