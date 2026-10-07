# SQL Assistant

`sql_assistant` is one native Nanobot skill with two deliberately separate
flows. Existing corporate scripts are retrieved by verified identifiers and
delivered verbatim. New SQL is grounded in KB tables/examples/columns, generated
through the shared LLM client, statically validated, optionally analyzed by
Spark without an action, repaired at most twice, and explained from AST facts.
No workflow executes SQL against business data.

## Runtime architecture

`PgDuckDbSyncService` mirrors the three labelled `s_grnplm_ld_audit_da_project_34.kb_*` tables into the
existing `DuckDbCacheStore`. Runtime tools receive that store through the
existing `set_provider` DI hook. `KbStore` is the only runtime module containing
physical KB SQL. Hybrid retrieval accepts records during offline build and the
gateway searches only atomically published persistent BM25/FAISS corpus builds.
Deleted DuckDB IDs are never returned from a stale build.

Spark analysis is optional and lazy. BGE inference runs through the existing
Appeals Osiris GPU worker; gateway and builder do not load model weights locally.
Tables/examples use BM25 plus BGE-M3/FAISS; columns are BM25-only.
Reranking runs at request time, not during index construction.

## Data and deployment

1. Apply `sql/sql_assistant/001_create_kb.sql` in the target Greenplum database.
   It uses the existing `s_grnplm_ld_audit_da_project_34` schema and never creates
   a schema. The four indexes must be applied once on Greenplum 6.
2. Run `bootstrap_kb.py --dry-run` against independent legacy examples and the
   metadata catalog. Review row counts, invalid metadata and duplicate columns.
3. Repeat without `--dry-run` to upsert examples, tables and columns in one
   transaction. `load_metadata.py` remains available for explicit CSV/JSONL
   sources; `migrate_legacy_examples.py` can migrate examples independently.
4. Run `derive_groups.py`, `enrich_summary.py`, `enrich_nl.py`, and optionally
   `enrich_types.py`; use `--state-file` for batch resume and `--dry-run` first.
5. Let the existing `PgDuckDbSyncService` publish a DuckDB snapshot.
6. Build `tables`, `examples`, and `columns` indexes with an explicit snapshot
   and index root.
7. Run the CLI smoke tests below.
8. Deploy with `sql_assistant` enabled; the legacy `sql-analyzer` skill is kept
   on disk but disabled in `agents.defaults.disabledSkills`.
9. Confirm gateway registration of `sql_analyzer`, `kb_search`, `kb_describe`,
   `sql_generate`, `sql_validate`, and `sql_facts`.

## Bootstrap from the existing corporate sources

The independent source tables are:
- Examples: `s_grnplm_ld_audit_da_project_34.70_aam_scripts_examples`
  (override with `--source-table`).
- Metadata: `s_grnplm_ld_audit_da_sandbox_oarb.dvb_kav_repl_test`
  (override with `--metadata-table`).

The target defaults to `s_grnplm_ld_audit_da_project_34` (`--target-kb-schema`).
Admin entrypoints use `--profile prod|test` to initialize settings, then
the shared `workspace.utils.db.resolve_dsn()` reads `channels.postgres.dsn`.
A direct DSN in project.json works without env; `--dsn-env NAME` is an optional
explicit legacy override. The DSN is never printed.
The KB DDL must already exist before apply; dry-run needs no target tables.

Legacy examples retain script_id/km_id/file_path/file_name/script_body/script_summary.
Original SQL is preserved byte-for-byte as a Python string and is never executed.
`physical_tables()` populates only `kb_examples.tables` with external read
dependencies. Its extraction algorithm is unchanged. Parse failures increment
`examples_parse_errors` and leave the table list unknown (an existing list is
preserved on update). Empty, invalid or unparsable examples do not filter metadata.

Metadata is read independently, with fetch batches of `--batch-size` (default 500).
Every valid metadata row contributes to a normalized (lowercase, trimmed)
schema/table/field key. NULL or empty schema/table/field names are counted as
`metadata_invalid_rows` and that entire row is skipped.
There is no matching against example SQL and no version selection.

One `kb_tables` row is created per normalized schema/table pair:
`table_name = schema_name + "." + table_name`, description from `table_descr`,
dialect `spark`. `schema_descr` is not stored. Columns use `field_name`,
`field_type`, `field_descr`, and NULL ordinal. The table summary contains
the first `--summary-columns` columns (default 50), ordered by normalized field
name, formatted as `field_name:field_type` (UNKNOWN for absent types).

Table IDs hash `lower(schema_name) + "." + lower(table_name)`;
column IDs hash that name plus `"." + lower(field_name)`.
Both use BLAKE2b with an 8-byte digest, mask to positive 63-bit BIGINT,
and map zero to one. Column table_id is its parent's synthetic ID.
Distinct logical keys sharing an ID raise `SyntheticIdCollisionError` before
any writes, including ambiguous concatenations of dotted identifiers.
IDs no longer use the old namespace-prefixed hash format: review/rebuild
existing metadata KB rows in the closed deployment before applying this version.
Bootstrap never automatically deletes or migrates old rows; DDL is unchanged.

Repeated column keys increment `duplicate_columns` for each extra occurrence.
Identical metadata is deduplicated; casing differences use deterministic
display names. Conflicting duplicate column metadata or conflicting non-empty
table descriptions raise a clear error before any writes.

Dry-run reports `examples_read`, `examples_parse_errors`, `examples_to_upsert`,
`metadata_rows_read`, `metadata_invalid_rows`, `unique_tables`, `unique_columns`,
`tables_to_upsert`, `columns_to_upsert`, `duplicate_columns`, and `dry_run`.
For the supplied catalog, expected counts are 5049 tables and 105910 columns
with zero duplicates; these counts require verification on the closed network.

Apply preserves the existing bulk-upsert: parameterized execute_values loads a
temporary staging table, then UPDATE/INSERT joins run in the caller's transaction.
Existing enrichment is preserved. psycopg2 is imported only on write; plan and
fake tests do not require the driver.
Duplicate legacy script IDs are skipped. No rows are deleted.
Run one bootstrap writer at a time. CLI dry-run sets a read-only transaction,
performs only source SELECTs, and rolls back. CLI returns exit 2 for invalid
metadata, duplicate columns or invalid/duplicate/null-SQL examples; legacy parse
errors alone do not cause a nonzero exit or block metadata creation.

From the repository root, with the project Python environment active:

```bash
python -m workspace.skills.sql_assistant.scripts.bootstrap_kb --profile prod --dry-run
python -m workspace.skills.sql_assistant.scripts.bootstrap_kb --profile prod
python -m workspace.skills.sql_assistant.scripts.build_index --profile prod --source duckdb --duckdb /explicit/cache.duckdb --index-root /explicit/index --corpus all
```

The build must use the published snapshot after the generic sync has copied the
new KB tables. Set the same index/model paths in `gateway.kb_search`; rebuilding
indexes does not replace the generic sync. For a direct GP build, use `--profile prod --source gp`; it uses the same shared project DSN resolver.

## Generated SQL delivery gate

The screenshot showed semantic invalid results despite successful tool-audit calls.
Audit `status=ok` means the function returned; it does not mean SQL is valid.
Public generate/validate results now carry `valid`, `publishable`, and
`delivery.action/instruction`. Invalid results retain issues/warnings but expose
no ready SQL, AST SQL, failed-repair SQL, or success facts. Internal SQL remains
available only for at most two repair attempts; repeated SQL/issues stop early.
Only a validated version can produce final SQL and facts.

Requested live Spark analysis must complete successfully; invalid/unavailable/
timeout blocks delivery. `validation_scope=static_only` is not a claim of real DB
execution. Ready scripts retain their separate verbatim contract. The skill and
workflow prohibit reconstructing hidden failed SQL from history or prior_sql.
This is a tool/skill-level gate, not a new generic outbound filter.

## Existing Osiris integration

The shared NFS transport/profile/lifecycle helpers are reused from d3_nanobot.
The existing `workspace/skills/appeals-analyzer/rerank_osiris_worker.py` gains
`embed` and retains retrieve/rerank. No new osiris_job.py, image, service name,
NFS service or GPU job is created by SQL Assistant.

`gateway.kb_search.osiris` defaults:
- service_name: appeals; job_name: srbd3
- nfs_root: /home/datalab/nfs/audit_nanobot/workspace/data_store/osiris/srb_d3
- heartbeat_max_age_sec: 12; request_timeout_sec: 1800; batch_size: 256

Use the same NFS root visible to gateway/builder and the existing container.
The consumer verifies a compatible READY/busy heartbeat AND SDK Running state.
It uses submit_request/wait_result with `auto_recover=False`; stopped job, stale
heartbeat, missing capability, handler failure or timeout returns an explicit
error. There is no auto-create/delete/restart or silent local inference fallback.

The worker loads existing local model directories once on CUDA:0:
`workspace/data_store/cache/caches_pipelines/BAAI:bge-m3` and
`workspace/data_store/cache/caches_pipelines/bge-reranker-v2-m3`.
Existing APPEALS_BGE_MODEL_PATH / APPEALS_RERANKER_MODEL_PATH overrides remain.
Dependencies and weights must already exist in the current image/mount.
Automatic package installation and embedded package-index credentials were NOT
copied. Normal operation never downloads models.

Embedding requests carry IDs/texts; results are checked for model identity,
exact ID population, 1024 dimensions and finite nonzero vectors, then normalized.
Rerank reuses the loaded model, raw logits plus sigmoid, exact ID correlation and
finite [0,1] scores. Reordered responses are restored to input order.
Empty batches issue no request. Appeals callers retain their DataFrame rerank
response; SQL Assistant requests records. Appeals retrieve initializes its domain
indexes lazily on first retrieve; its full domain files/cache must already be
deployed if that capability is used.

BM25/FAISS build stays on the builder, not in GPU inference. Atomic CURRENT and
three-corpus storage format are unchanged. Builder reads keyset pages over the
whole KB, verifies COUNT/unique ordered IDs and unchanged source signature before
publication. Columns are no longer cut at 100000. The cheap count/MAX(updated_at)
signature assumes normal tracking updates; use a stable snapshot while building.

### Closed-contour rollout

1. Transfer the commit files. Merge project.json: preserve the REAL direct DSN;
   do not replace it with the external project's placeholder.
2. Update the existing worker/config/transport at the deployed paths. Arrange
   operator-managed reload/start of the SAME service with the existing operator
   tooling. SQL Assistant never restarts it itself. A running process will not
   gain embed capability merely because its file was copied.
3. Verify CUDA weights/dependencies, SDK Running, heartbeat capabilities
   retrieve/rerank/embed, paths and shared NFS. Existing Appeals domain code/cache
   is not included as a new skill in this change.
4. Review old-ID migration; run bootstrap with --profile prod --dry-run, then
   explicitly apply. All enrichment/admin scripts accept --profile.
5. Wait for existing DuckDB sync; build all corpora from the same published
   snapshot. Compare manifest counts against source counts.
6. Use identical index_root/model names in gateway.kb_search. Legacy device and
   model_cache_dir flags are compatibility-only, not local inference switches.
7. Check invalid generation: no SQL block, visible reasons. Valid delivery must
   carry publishable=true.

Lexical-only builder: `--lexical-only`. Gateway lexical-only requires explicitly
disabling both dense_enabled and reranker_enabled; service errors are NOT fallback.
CLI tables/examples search uses Osiris (`--profile prod`); `--lexical-only` is an
explicit diagnostic mode.

## External-network verification

```bash
python -B -m unittest tests.test_sql_assistant_bootstrap tests.test_sql_assistant_dependencies tests.test_sql_assistant_delivery tests.test_sql_assistant_admin_dsn tests.test_sql_assistant_osiris tests.test_sql_assistant_index_complete tests.test_sql_assistant_tool_adapters -v
```

Tests cover invalid/successful repairs, DSN delegation/read-only dry-run, existing
worker readiness, NFS correlation, embedding shape/order, sigmoid rerank,
no-create/error/timeout paths, and a complete 105910-column corpus.
They use mocks/fakes and local temporary files, not infrastructure.
Real sqlglot, Spark, CUDA, weights, GP6 bulk staging and SDK/NFS require separate
closed-contour verification. OpenSpec CLI is absent here: artifacts were maintained
manually and checked structurally. Full pytest needs the deployment dependencies.

## Automated checks

```bash
python -m pytest tests/test_sql_assistant_bootstrap.py tests/test_sql_assistant_ready.py tests/test_sql_assistant_kb_store.py tests/test_sql_assistant_hybrid.py tests/test_sql_assistant_static.py tests/test_sql_assistant_tools.py tests/test_sql_assistant_integration.py tests/test_config_keys.py
python -c "import workspace.tools.sql_analyzer, workspace.tools.kb_search, workspace.tools.kb_describe, workspace.tools.sql_generate, workspace.tools.sql_validate, workspace.tools.sql_facts"
python -m unittest tests.test_sql_assistant_dependencies tests.test_sql_assistant_bootstrap -v
```

Bootstrap unit tests isolate SQL extraction and test independent metadata/upserts with an
in-memory DB double. Dependency tests exercise CTAS/INSERT targets, intermediate
chains, scoped CTEs, multi-statement SQL and the Hive fallback without DB I/O.
The explicit sqlglot scope test is skipped when sqlglot is not installed.
Static tests exercise the real sqlglot extraction; the tool
tests also exercise all six imports and the native project-tool registration/DI.
Real Greenplum permissions and catalog contents still require a reviewed dry-run.

## Smoke tests without gateway

```bash
bash workspace/skills/sql_assistant/sql_assistant.sh --mode smoke --dialect spark
bash workspace/skills/sql_assistant/sql_assistant.sh --mode smoke --duckdb /explicit/cache.duckdb
bash workspace/skills/sql_assistant/sql_assistant.sh --mode ready --duckdb /explicit/cache.duckdb --prompt "покажи script_id 338"
bash workspace/skills/sql_assistant/sql_assistant.sh --mode search --duckdb /explicit/cache.duckdb --index-root /explicit/index --profile prod --corpus tables --prompt "обращения по месяцам"
bash workspace/skills/sql_assistant/sql_assistant.sh --mode validate --dialect spark --sql-file query.sql
```

Index build:

```bash
python3 -m workspace.skills.sql_assistant.scripts.build_index --source duckdb --duckdb /explicit/cache.duckdb --index-root /explicit/index --corpus all --profile prod
python3 -m workspace.skills.sql_assistant.scripts.build_index --source gp --index-root /explicit/index --corpus all --profile prod
python3 -m workspace.skills.sql_assistant.scripts.build_index --source duckdb --duckdb /explicit/cache.duckdb --index-root /explicit/lexical-index --corpus all --lexical-only
```

## Legacy gap audit

Transferred/adapted: exact ready-script invariants; direct DataFrame/record
preparation; BM25 retrieval, group collapse, `rank_ids`, deterministic IDs,
atomic `CURRENT`/manifest builds and dropped-row accounting; SQL extraction and
Spark datetime sanitizer; bounded generate/validate/fix workflow; lazy Spark
logical analysis/catalog columns; deterministic AST facts and error categories.

Intentionally excluded: FastAPI/UI, `/execute`, result CSV/parquet jobs, job
registry/cancellation/notifier/presence, HTTP sessions, legacy JSON CRUD,
`rag_service`, legacy GigaChat client, runtime pip installation, and every direct
runtime GP connection. The old project is reference source only and is neither
imported nor executed.

Remaining production validation: real KB sources and content quality; existing
Osiris BGE-M3/reranker weights, CUDA memory, shared NFS and inference latency
(BM25-only requires explicit disabling of dense/reranking); Spark/JVM,
metastore and memory smoke; Greenplum example quality and GP6 compatibility;
hold-out labels/expert review. The exporter can derive facts from available
examples, but does not fabricate missing golden labels.
