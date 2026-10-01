# Appeals production integration

## Two explicit backend paths

Native `workspace/tools/appeals_analyzer.py` forces request-local `backend_scope("cache")`:

`frontend → structural SQL in shared DuckDB → allowed app_row_id → Osiris retrieval → DuckDB hydration → Osiris rerank → score >= 0.5 → four hypotheses + XLSX + MessageTool`.

`appeals_analyze.sh --profile prod` / `scripts/cli.py` explicitly enters `backend_scope("greenplum")`. It initializes the existing workspace DB pool, executes SQL through that pool, and shuts it down on success or failure. It does not initialize Gateway or a DuckDB cache. The retrieval/report code is shared. Neither environment variables nor a DuckDB error switch the native Tool to GP.

`utils/data_store.py` delegates cache construction to `lib.core.skill_config.build_cache_provider("appeals_analyzer", ...)`. The provider resolves the shared `TableRegistry.snapshot_path` and opens it read-only. No `refresh`, preload, private DuckDB creation or source-DB fallback is called. Opening waits at most `APPEALS_CACHE_WAIT_SECONDS` (default 60); required tables and source columns are validated before SQL use. Snapshot queries are serialized on the provider connection; async orchestration runs blocking work with `asyncio.to_thread`, preserving backend/artifact ContextVars.

## Production blocker: initial-only sync

The current shared `PgDuckDbSyncService._worker` performs an initial load, publishes it, then immediately enters `_poll_changes`. Its public configuration has no per-table initial-only mode. `TableEntry` accepts only `name/type/label/tracking_column`; a missing tracking column defaults to `updated_at`. No suitable tracking column has been confirmed for Appeals. Increasing the polling interval does not prevent the first poll. Incremental upsert also assumes an `id` column, whereas Appeals uses `app_row_id` and related tables have multiple rows per appeal.

Therefore `project.json::skills.appeals_analyzer` declares all three source tables but remains **enabled: false**. No invented tracking column, global polling change or core patch was introduced. This disables automatic resource registration, not the independently configured native Tool. The Tool can read an already prepared shared snapshot; on a clean deployment the Appeals tables will be absent and the Tool will fail explicitly. **Do not enable this declaration as a production rollout workaround.** Safe initial-only support in the shared sync API is required before activating registration and performing the first supported snapshot load.

Current declarations use schema `s_grnplm_ld_audit_da_project_27`:

- `40_kaluginvs_anofl_appeal_2026`
- `40_kaluginvs_anofl_appeal_dialogs_2026`
- `40_kaluginvs_anofl_appeal_task_2026`

The shared initial loader copies full physical tables (`SELECT *`); it has no source population predicate API. The text-bearing population predicate is therefore applied in Appeals structural SQL, using nonempty `req_desc` or an `EXISTS` dialogue with nonempty chat/CRM text. No claim is made that the physical snapshot contains only the text-bearing subset. Initial-load storage/RAM must account for all three full source tables. Additional production years can be declared as complete table triplets; 2023/2024 are not added.

## Search and hydration

Canonical frontend `prd/s_prd/chnl` values are exact values, independent groups use AND and values within a group use IN. Empty groups add no condition. Canonical/JSON dates are authoritative; SQL uses inclusive start and exclusive next-day end. The legacy CSV parser alone uses the old dictionary and legacy date extraction.

`app_row_id` drives structural results, joins, search, sessions and final IDs; `id` is its string alias. Dialogues and tasks are read separately, aggregated before joining, avoiding a tasks × dialogues Cartesian product. Canonical text is description plus PPRB chat, falling back to CRM call only when PPRB is empty. The same helper feeds reranker, hypothesis evidence and follow-up details.

Osiris loads BGE-M3, BGE-reranker-v2-m3, global FAISS, BM25 shards and ID mapping once. Both models require CUDA:0; no multi-GPU placement or CPU model fallback is used. FAISS attempts GPU conversion with an allowed-selector probe; unsupported conversion/search retains CPU FAISS inside Osiris. BM25 runs in CPU RAM in that worker.

Allowed IDs map to global positions. FAISS receives `IDSelectorBatch` via `SearchParametersIVF`; BM25 receives each shard's slice as `weight_mask`. Missing vector IDs are skipped. No subset index or corpus retokenization is performed. Metadata need not contain texts/tokenized corpus; embeddings.memmap is not loaded online.

Reference parameters: `FAISS_K=2048`, `BM25_TOTAL_K=1372`, `ALPHA=0.3`, `K_RRF=60`. Reference shard quotas, shard-local ranks and missing-rank handling are preserved. The full fused candidate pool is hydrated and reranked. Logits receive sigmoid once; all scores >= 0.5 are retained. No passing document means no XLSX or hypotheses, and no top-2048 fallback.

## NFS, startup and delivery

Osiris lifecycle is shared by `workspace/utils/osiris_runtime/`, used by both the root operator CLI and Appeals requests. `utils/srb_d3.py` is the Appeals-specific retrieve/rerank adapter. Requests auto-start an absent or terminal job under the shared NFS lock and wait up to `OSIRIS_START_WAIT_TIMEOUT_SEC` (default 300 seconds). Startup failure returns a retry-later message using `OSIRIS_RETRY_AFTER_SEC` (default 900 seconds). The worker exits after `OSIRIS_IDLE_TIMEOUT_SEC` (default 3600 seconds) without requests; each completed request renews the full TTL. See [shared runtime guide](../../../docs/OSIRIS_RUNTIME.md).

Run from the repository root:

```bash
# start
python appeals_osiris_job.py start

# status (read-only)
python appeals_osiris_job.py status

# Gateway starts and stops separately; requests may auto-start Osiris.
python appeals_osiris_job.py stop
```

The confirmed closed-contour stop call is `osiris.delete(actual_job_name)`.
The generic `python osiris_job.py start|status|stop` CLI uses the same profile.

`start` prepares NFS directories, checks SDK state and heartbeat, reuses Pending/Running jobs, and creates only after confirmed absence/terminal state. Manual and automatic starts share the five-minute default readiness deadline (`start --startup-timeout N` for a manual override). The create call sets `restart=False` so normal worker exit can release the GPU; verify this behavior against the closed-contour scheduler. An unconfirmed create keeps `create_pending` metadata and does not authorize a duplicate.

`status` never writes files or invokes create/stop. It prints requested/actual names, SDK state, heartbeat age/status/PID/protocol/capabilities, GPU counts, queue size, active requests and idle timing. An absent job is a normal result; an indeterminate SDK result is not evidence that creating another job is safe.

`stop` resolves the actual name and calls confirmed `osiris.delete(name)`. It polls state/status until terminal/not-found (`stop --timeout N`, default 120 seconds), then removes lifecycle metadata/heartbeat. Missing/terminal jobs are idempotent success. Confirmation timeout preserves metadata.

CLI and skill auto-start serialize lifecycle mutations with the same NFS `launcher.lock`. Waiting callers re-check the job after the lock is released and do not create duplicates. A lock from a confirmed-dead same-host PID can be reclaimed; unknown owners are not assumed dead. Scheduler readiness waits occur after the create lock has been released.

`utils/osiris_config.py` is the stdlib-only Appeals profile for job name, pool, image, worker path, NFS root, heartbeat paths, protocol, capabilities and default timeouts.
Supported deployment overrides:

- `APPEALS_OSIRIS_JOB_NAME`
- `APPEALS_OSIRIS_POOL`
- `APPEALS_OSIRIS_IMAGE`
- `APPEALS_OSIRIS_WORKER_SCRIPT`
- `APPEALS_OSIRIS_NFS_ROOT`

Configure path and job overrides in launcher, Gateway/CLI and the Osiris worker image/environment. Only the confirmed `osiris.create` parameters are passed; an undocumented SDK environment-forwarding argument is not invented. The idle TTL is written into shared metadata for the worker. Closed-contour image and credential values already present in the deployment config are preserved; never log or commit credentials.

NFS protocol v2 distinguishes `retrieve` and `rerank`, correlates UUID request IDs/types, atomically publishes payload/manifest/result, and checks expiry. Client and worker remove request artifacts after completion, error or timeout. Timed-out in-flight work cannot publish a result after expiry. Heartbeat readiness requires protocol v2 and both capabilities; an already running old worker must be replaced through the explicit operator lifecycle procedure. The code does not create a duplicate job to bypass that incompatibility.

Worker environment retains the existing closed-contour model/cache paths and `APPEALS_RAG_CACHE_DIR`, `APPEALS_RAG_META_FILE`, `APPEALS_BGE_MODEL_PATH`, `APPEALS_RERANKER_MODEL_PATH` overrides. These must be available inside Osiris, not merely in the Gateway shell. At startup the closed-contour worker installs its existing pinned FAISS, Hugging Face, pandas, sentence-transformers, rank_bm25 and bm25s dependencies before model loading. It reads its existing `TOKEN_OSC` from `utils/osiris_config.py`; that credential must remain private. The token is supplied to pip through the subprocess environment rather than command-line arguments. Installation failures prevent READY. CUDA PyTorch and other base dependencies remain the responsibility of the Osiris image.

XLSX is written under `workspace/data_store/cache/sessions/<safe_session_key>/results/`. Only the file registered in the current artifact scope is attached; directories are not scanned. The native Tool resolves the actual request session key and sends full report + media through the existing MessageTool. A runtime request without a session key fails before execution. Standalone CLI uses its explicit `--session-id`. The legacy `filters` argument remains accepted but unused; the full prompt owns structural filters.

## Verification

Offline suite (numpy, pandas, openpyxl and pydantic; no Nanobot/ML/DB installation needed):

```bash
python -B tests/test_appeals_osiris_lifecycle.py
python -B tests/test_appeals_production_contracts.py
```

The suite exercises production orchestration, SQL generation, explicit backend isolation, reference masking/RRF on test doubles, score/ID correlation, real XLSX serialization, NFS round trips/expiry/cleanup, safe imports and native delivery with a minimal Nanobot API double. The optional reference ZIP comparison skips when the undistributed archive is absent.

With the repository development environment installed:

```bash
python -m pytest tests/test_appeals_production_contracts.py tests/test_appeals_hybrid_contracts.py tests/test_tools_appeals_analyzer.py tests/test_appeals_standalone_cli.py
```

Closed-contour acceptance remains required for actual source/snapshot schema and volume, GPU model loading and FAISS selector support, large global-cache memory consumption, Osiris SDK/NFS behavior, GigaChat hypotheses and end-to-end channel attachment delivery. Offline doubles do not prove those deployment properties.
