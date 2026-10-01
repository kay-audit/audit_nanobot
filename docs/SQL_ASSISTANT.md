# SQL Assistant

`sql_assistant` is one native Nanobot skill with two deliberately separate
flows. Existing corporate scripts are retrieved by verified identifiers and
delivered verbatim. New SQL is grounded in KB tables/examples/columns, generated
through the shared LLM client, statically validated, optionally analyzed by
Spark without an action, repaired at most twice, and explained from AST facts.
No workflow executes SQL against business data.

## Runtime architecture

`PgDuckDbSyncService` mirrors the three labelled `sqlagent.kb_*` tables into the
existing `DuckDbCacheStore`. Runtime tools receive that store through the
existing `set_provider` DI hook. `KbStore` is the only runtime module containing
physical KB SQL. Hybrid retrieval accepts records during offline build and the
gateway searches only atomically published persistent BM25/FAISS corpus builds.
Deleted DuckDB IDs are never returned from a stale build.

Spark and model libraries are lazy capabilities. `sentence-transformers==6.0.1`
provides BGE-M3 dense retrieval and the cross-encoder reranker and is loaded only
on the first `kb_search` call. Set `gateway.kb_search.model_cache_dir` to the
same directory passed to `download_models.py --cache-dir` and
`build_index.py --model-cache-dir`; request-time loading remains
`local_files_only=True`. Greenplum generation
uses the sqlglot `postgres` parser and is experimental/static-only. No runtime
tool opens Greenplum or invents a cache path.

## Data and deployment

1. Apply `sql/sql_assistant/001_create_kb.sql` in the target Greenplum database.
2. Migrate legacy examples with `migrate_legacy_examples.py --dry-run`, review
   duplicate/null statistics, then repeat without `--dry-run`.
3. Load real table/column metadata with `load_metadata.py --kind tables|columns
   --input ... --dry-run`, then without `--dry-run`. Source filenames/table names
   are environment-specific and intentionally not invented here.
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

## Smoke tests without gateway

```bash
bash workspace/skills/sql_assistant/sql_assistant.sh --mode smoke --dialect spark
bash workspace/skills/sql_assistant/sql_assistant.sh --mode smoke --duckdb /explicit/cache.duckdb
bash workspace/skills/sql_assistant/sql_assistant.sh --mode ready --duckdb /explicit/cache.duckdb --prompt "покажи script_id 338"
bash workspace/skills/sql_assistant/sql_assistant.sh --mode search --duckdb /explicit/cache.duckdb --index-root /explicit/index --corpus tables --prompt "обращения по месяцам"
bash workspace/skills/sql_assistant/sql_assistant.sh --mode validate --dialect spark --sql-file query.sql
```

Index build:

```bash
python3 -m workspace.skills.sql_assistant.scripts.download_models --cache-dir /explicit/models
python3 -m workspace.skills.sql_assistant.scripts.build_index --source duckdb --duckdb /explicit/cache.duckdb --index-root /explicit/index --model-cache-dir /explicit/models --corpus all --device cpu
python3 -m workspace.skills.sql_assistant.scripts.build_index --source gp --gp-dsn-env DATABASE_URL --index-root /explicit/index --model-cache-dir /explicit/models --corpus all --device cpu
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

Remaining production validation: real KB sources and content quality; BGE-M3
model download, memory and latency in the gateway (BM25 remains the deterministic
fallback when dense/reranking is disabled); Spark/JVM,
metastore and memory smoke; Greenplum example quality and GP6 compatibility;
hold-out labels/expert review. The exporter can derive facts from available
examples, but does not fabricate missing golden labels.
