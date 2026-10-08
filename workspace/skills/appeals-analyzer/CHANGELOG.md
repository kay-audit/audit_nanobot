# Changelog

## Unreleased — report minimum 500

- Reranker returns all scored candidates. Only production/standalone report selection applies strict score > 0.5 without a cap; when fewer pass, the best remaining candidates top up the report to `PipelineConfig.report_min_items` (500). Reports with fewer available candidates retain all of them. Input/output counts and selection counts are logged; candidate count loss raises an error. The unused legacy threshold helper was removed. Hypothesis text sampling, retrieval, scoring and structural hydration constraints are unchanged.

## Unreleased — shared Osiris runtime and idle lifecycle

- Generic `workspace/utils/osiris_runtime` now owns profile-based SDK discovery, lifecycle lock, start/status/stop, typed errors, NFS transport and worker idle accounting. `osiris_job.py` is the operator CLI; `appeals_osiris_job.py` remains a compatibility wrapper.
- Manual and request-triggered start share one implementation. `osiris.create(..., restart=False)` prevents an idle-exited worker from being relaunched; confirmed `osiris.delete(actual_name)` handles operator stop.
- Idle TTL defaults to 3600 seconds and resets after each request. Startup wait defaults to 300 seconds, while retrieve/rerank execution deadlines remain separate. Appeals returns a 15-minute retry message only for startup unavailability.
- Existing closed-contour image, token source and pinned worker packages are retained; pip receives the private index through environment rather than argv. BGE/FAISS/BM25/RRF/reranker logic is unchanged.


## Unreleased — shared snapshot and Osiris production pipeline

- Native Tool reads only the shared Gateway DuckDB snapshot; standalone CLI explicitly retains direct Greenplum with `--profile` and its existing pool lifecycle.
- Osiris protocol v2 supports retrieval and rerank on one CUDA GPU; Gateway imports no ML/index runtime. Imports no longer start Osiris.
- Global FAISS selectors and BM25 shard masks preserve reference RRF parameters (2048/1372, 0.3/60); no subset indexes or threshold fallback.
- Canonical frontend values/dates are authoritative; independent structural groups use AND. Canonical IDs and joins use `app_row_id`.
- Hydration retains CRM dialogue fallback and task records without Cartesian multiplication. One text helper feeds reranking and evidence.
- All scores >= 0.5 feed four hypotheses and one session-scoped XLSX, delivered with the full report through MessageTool. Follow-up uses final IDs.
- Appeals table registration is declared but disabled pending a safe shared initial-only sync API; see PRODUCTION.md. No Nanobot core change or fake tracking column.
- Offline production contracts cover SQL/backend separation, masking, score/ID correlation, request cleanup, session/media and CLI behavior.

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
