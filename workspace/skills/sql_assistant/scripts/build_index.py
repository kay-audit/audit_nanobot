from __future__ import annotations

import argparse
import json

from lib.services.hybrid_search import build_index, prepare_from_frame
from lib.services.kb_store import KbStore
from workspace.skills.sql_assistant.scripts._offline import DuckDbProvider, PostgresProvider
from workspace.skills.sql_assistant.scripts._pg_admin import add_connection_arguments, connect, initialize_settings
from workspace.skills.sql_assistant.scripts.osiris_adapter import configured_models

FIELDS = {
    "tables": (("description", "columns_summary", "table_name"), "group_key", ("id", "group_key", "dialect")),
    "examples": (("nl", "nl_variants", "script_description", "file_name", "file_path"), None, ("id", "dialect", "tables")),
    "columns": (("table_name", "column_name", "data_type", "description"), None, ("id", "table_id")),
}


def build_corpus(store, index_root, corpus, *, models=None, dense_model="BAAI/bge-m3", page_size=5000):
    signature = store.current_source_signature(corpus)
    expected_count = int(json.loads(signature)["row_count"])
    rows = store.complete_corpus_frame(corpus, page_size=page_size)
    if len(rows) != expected_count:
        raise ValueError(f"{corpus} corpus is incomplete: read {len(rows)}, expected {expected_count}")
    row_ids = [str(row.get("id") or "") for row in rows]
    if len(row_ids) != len(set(row_ids)) or any(not value for value in row_ids):
        raise ValueError(f"{corpus} contains duplicate or empty IDs")
    fields, group, metadata_fields = FIELDS[corpus]
    docs = prepare_from_frame(rows, text_fields=fields, group_field=group, metadata_fields=metadata_fields)
    vectors = {}
    if corpus != "columns" and models is not None:
        encoded = models.embed([doc.text for doc in docs])
        if len(encoded) != len(docs):
            raise ValueError("Embedding/document count mismatch")
        vectors = {doc.id: [float(value) for value in encoded[position]] for position, doc in enumerate(docs)}
    if signature != store.current_source_signature(corpus):
        raise ValueError(f"{corpus} source changed during build; no index was published")
    return build_index(
        index_root, corpus, docs, source_signature=signature, source_hash=store.rows_hash(rows),
        model_versions={"lexical": "bm25s", "dense": dense_model if vectors else None, "device": "osiris" if vectors else None},
        dropped_rows=len(rows) - len(docs), dense_vectors=vectors,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build complete SQL Assistant indexes with existing Osiris inference")
    parser.add_argument("--corpus", choices=("tables", "examples", "columns", "all"), default="all")
    parser.add_argument("--index-root", required=True)
    parser.add_argument("--source", choices=("duckdb", "gp"), default="duckdb")
    parser.add_argument("--duckdb")
    add_connection_arguments(parser)
    parser.add_argument("--gp-dsn-env", dest="dsn_env", help="Legacy explicit DSN environment override")
    parser.add_argument("--dense-model", default="BAAI/bge-m3")
    parser.add_argument("--page-size", type=int, default=5000)
    parser.add_argument("--lexical-only", action="store_true")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), help="Compatibility flag; inference always uses existing Osiris")
    parser.add_argument("--model-cache-dir", help="Compatibility flag; gateway never loads model files")
    args = parser.parse_args(argv)
    if args.page_size < 1:
        parser.error("--page-size must be positive")
    if args.source == "gp":
        provider = PostgresProvider(connection=connect(args))
    else:
        if not args.duckdb:
            parser.error("--duckdb is required for --source duckdb")
        provider = DuckDbProvider(args.duckdb)
    try:
        models = None
        if args.corpus != "columns" and not args.lexical_only:
            initialize_settings(args)
            models = configured_models(dense_model=args.dense_model)
        store = KbStore(provider)
        corpora = FIELDS if args.corpus == "all" else (args.corpus,)
        manifests = [build_corpus(store, args.index_root, corpus, models=models, dense_model=args.dense_model, page_size=args.page_size) for corpus in corpora]
        print(json.dumps(manifests, ensure_ascii=False, indent=2))
        return 0
    finally:
        provider.connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
