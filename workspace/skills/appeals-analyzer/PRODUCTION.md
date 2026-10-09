# Appeals production integration

## Two explicit backend paths

Native `workspace/tools/appeals_analyzer.py` forces request-local `backend_scope("cache")`:

`frontend → structural SQL in shared DuckDB → allowed app_row_id → Osiris retrieval → Greenplum candidate hydration → Osiris scores all candidates → report selection (all score > 0.5 plus top-up to 500) → four hypotheses + XLSX + MessageTool`.

`appeals_analyze.sh --profile prod` / `scripts/cli.py` explicitly enters `backend_scope("greenplum")`. It initializes the existing workspace DB pool, executes SQL through that pool, and shuts it down on success or failure. It does not initialize Gateway or a DuckDB cache. The retrieval/report code is shared. Neither environment variables nor a DuckDB error switch the native Tool to GP.

`utils/data_store.py` explicitly selects the population backend. Production resolves the existing snapshot path through `lib.core.skill_config.get_in_memory_cache_path`; structural lookup opens a short read-only connection, validates the exact schema and reads DISTINCT IDs with `fetchmany`. There is no snapshot wait, refresh or GP structural fallback. Hydration explicitly enters a GP scope independently of the population source. Blocking work runs through `asyncio.to_thread`, preserving backend/artifact ContextVars. The standalone prefilter retains its existing nonempty-description/dialog eligibility SQL and reads the original GP tables. Only production consumes the externally prepared population.

## Startup-only structural cache

`workspace/utils/appeals_structural_cache.py` implements an Appeals-specific loader over the shared DB pool. Before `ctx.start()` and before channels/agent accept requests, Gateway loads the eligible 2026 population into `main.appeals_structural_2026` and force-publishes the existing shared snapshot. Startup errors abort the gateway request lifecycle. The required `appeals_structural_cache` readiness component checks snapshot availability/schema. `gateway.appeals_analyzer.enable` (default true) enables both tool and loader; the existing testing runtime skips this production load.

Startup diagnostics use flushed stderr with the `[appeals-snapshot]` prefix, independently of logging filters. They show the prebuilt source table, its simple five-column SELECT, destination snapshot path, gateway OS PID and GP backend PID. LOAD START declares `prebuilt_source=true; no_text_transfer=true`. Each batch reports FETCH/INSERT timing and separate received/written row counts. Every 30 seconds a diagnostic thread reports the last known stage and its duration, including DB pool wait, cursor declaration, blocked FETCH, Arrow conversion, DuckDB insertion, snapshot publication and schema validation. A heartbeat is not evidence of GP progress; rising written row counts confirm completed inserts. `PUBLISH START`/`PUBLISH DONE`/`READY` distinguish ingestion from disk publication and readiness. Failures report the stage before connection cleanup. Diagnostics do not query GP again, transfer texts or enable retries/fallback.

`skills.appeals_analyzer.enabled` must remain **false**: its three source-table declarations are metadata, not generic sync resources. Enabling full-table registration raises a startup error. No Appeals table is added to `TableRegistry` or to periodic `PgDuckDbSyncService` polling. Generic sync/registry behavior is unchanged. The additive `DuckDbCacheStore.replace_arrow_batches(table, schema, batches)` API performs transactional ingestion, preserves duplicate rows and includes the resulting table in all subsequent ordinary snapshot publications.

Original tables for standalone prefilter and candidate hydration use schema `s_grnplm_ld_audit_da_project_27`:

- `40_kaluginvs_anofl_appeal_2026`
- `40_kaluginvs_anofl_appeal_dialogs_2026`
- `40_kaluginvs_anofl_appeal_task_2026`

The startup source is exclusively `s_grnplm_ld_audit_da_project_34.t_db_oarb_appeals_d3`, maintained manually by external ETL. It already contains the eligible 2026 population. Gateway does not create/update this table, calculate or verify eligibility, read original text tables, or add date predicates to ingestion. It selects only `app_row_id VARCHAR`, `req_reg_date TIMESTAMP`, `prd VARCHAR`, `s_prd VARCHAR`, `chnl VARCHAR`, casting the first two columns. There is no WHERE, DISTINCT, JOIN, EXISTS or regex in the startup SELECT. All source rows, including duplicate IDs, are retained. Missing source/columns, incompatible values, and unexpected cursor projection fail startup with the source name and expected columns. Eligibility correctness and source freshness are the responsibility of external ETL.

The shared-pool job uses a server cursor and bounded Arrow batches of 50,000 rows, including a typed empty table for an empty population. There is no full-result pandas DataFrame or startup `fetchall`. No periodic refresh is added: restart rebuilds this population. DuckDB performs columnar scans for filters/date and DISTINCT; no unmeasured ART index is added. GP execution plan, build time and memory for tens of millions of structural rows require closed-contour measurement. The existing Osiris protocol still requires an allowed-ID list; that list and DISTINCT working memory scale with eligible population. Ordinary generic snapshot publication copies the structural table again along with other resources.

## Search and hydration

Canonical frontend `prd/s_prd/chnl` values are exact values, independent groups use AND and values within a group use IN. Empty groups add no condition. Canonical/JSON dates are authoritative; SQL uses inclusive start and exclusive next-day end. The legacy CSV parser alone uses the old dictionary and legacy date extraction.

`app_row_id` drives structural results, joins, search, sessions and final IDs; `id` is its string alias. Dialogues and tasks are read separately, aggregated before joining, avoiding a tasks × dialogues Cartesian product. Canonical text is description plus PPRB chat, falling back to CRM call only when PPRB is empty. The same helper feeds reranker, hypothesis evidence and follow-up details.

After hybrid retrieval, the GP base hydration query repeats all original product/subproduct/channel/date constraints. Related dialogs/tasks are constrained by hydrated candidate IDs only. Defensive validation rejects contradictory base rows before the existing per-ID normalization; it cannot select an unrelated base row of the same ID. Filters/date are saved in session metadata and reused for follow-up hydration. Existing report/reranker unique-ID behavior is retained; the structural cache never deduplicates base rows.

Osiris loads BGE-M3, BGE-reranker-v2-m3, global FAISS, BM25 shards and ID mapping once. Both models require CUDA:0; no multi-GPU placement or CPU model fallback is used. FAISS attempts GPU conversion with an allowed-selector probe; unsupported conversion/search retains CPU FAISS inside Osiris. BM25 runs in CPU RAM in that worker.

Allowed IDs map to global positions. FAISS receives `IDSelectorBatch` via `SearchParametersIVF`; BM25 receives each shard's slice as `weight_mask`. Missing vector IDs are skipped. No subset index or corpus retokenization is performed. Metadata need not contain texts/tokenized corpus; embeddings.memmap is not loaded online.

Reference parameters: `FAISS_K=2048`, `BM25_TOTAL_K=1372`, `ALPHA=0.3`, `K_RRF=60`. Reference shard quotas, shard-local ranks and missing-rank handling are preserved. The full fused candidate pool is hydrated and reranked. Logits receive sigmoid once. Reranker returns every scored candidate, including low scores; the adapter requires exactly the input ID set. Gateway logs Reranker input/output and rejects count loss before report selection. Only `appeals_reports.select_accepted` applies the strict `score > PipelineConfig.score_threshold` (0.5) condition after scoring. It sorts numeric scores descending, preserves existing per-ID uniqueness and retains all above-threshold rows without an upper cap. If fewer pass, the best remaining candidates with score <= 0.5 top up the report to `PipelineConfig.report_min_items` (500). Final count is min(available unique candidates, max(above-threshold rows, 500)). Score exactly 0.5 does not pass but can participate in top-up. INFO `Appeals report selection` logs reranker input/output, above-threshold count, threshold, minimum and final count before export. Fewer available hydrated candidates means fewer final rows; unrelated IDs are not added. Invalid scores remain errors. Empty retrieval/hydration means no XLSX or hypotheses. This selection applies to both production and standalone, and final IDs drive follow-up. `hypothesis_sample_size` separately limits texts sent for hypothesis evidence. The unused `bge_search_engine.select_threshold_or_fallback` helper has been removed.

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

Worker environment retains the existing closed-contour model/cache paths and `APPEALS_RAG_CACHE_DIR`, `APPEALS_RAG_META_FILE`, `APPEALS_BGE_MODEL_PATH`, `APPEALS_RERANKER_MODEL_PATH` overrides. These must be available inside Osiris, not merely in the Gateway shell. At startup the closed-contour worker installs its existing pinned FAISS, Hugging Face, pandas, sentence-transformers, rank_bm25 and bm25s dependencies before model loading. It reads the existing `APPEALS_OSIRIS_PIP_INDEX_URL` / `APPEALS_OSIRIS_PIP_TRUSTED_HOST` from `utils/osiris_config.py`; these are read from the environment and are empty by default, so the worker installs from public PyPI. Any credential for a closed contour must be supplied through the environment and must remain private. Installation failures prevent READY. CUDA PyTorch and other base dependencies remain the responsibility of the Osiris image.

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
python -m pytest tests/test_appeals_structural_cache.py tests/test_appeals_production_contracts.py tests/test_appeals_hybrid_contracts.py tests/test_tools_appeals_analyzer.py tests/test_appeals_standalone_cli.py tests/test_duckdb_cache_store.py
```

Structural tests use real DuckDB/Arrow and an offline GP cursor. The unchanged prebuilt SELECT is executed against DuckDB fixtures to verify projection/casts and duplicate retention. Missing tables, each missing required column, malformed timestamps, incorrect cursor projections and publication/schema failures are exercised without source fallback. These are SQL/unit contracts, not actual Greenplum execution. Closed-contour acceptance remains required for prebuilt GP source existence/permissions/schema/freshness, named-cursor support, read/build time, memory/snapshot volume, GPU model loading and FAISS selector support, Osiris SDK/NFS behavior, GigaChat hypotheses and end-to-end channel delivery.
