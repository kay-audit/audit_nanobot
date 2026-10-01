from __future__ import annotations

import argparse
import json
from pathlib import Path

from lib.services.hybrid_search import build_index, make_bge_embedder, prepare_from_frame
from lib.services.kb_store import KbStore
from workspace.skills.sql_assistant.scripts._offline import DuckDbProvider, PostgresProvider

FIELDS = {
    "tables": (("description", "columns_summary", "table_name"), "group_key", ("id", "group_key", "dialect")),
    "examples": (("nl", "nl_variants", "script_description", "file_name", "file_path"), None, ("id", "dialect", "tables")),
    "columns": (("table_name", "column_name", "data_type", "description"), None, ("id", "table_id")),
}


def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument("--corpus",choices=("tables","examples","columns","all"),default="all"); p.add_argument("--device",choices=("auto","cpu","cuda"),default="auto"); p.add_argument("--index-root",required=True); p.add_argument("--source",choices=("duckdb","gp"),default="duckdb"); p.add_argument("--duckdb"); p.add_argument("--gp-dsn-env"); p.add_argument("--dense-model",default="BAAI/bge-m3"); p.add_argument("--model-cache-dir"); p.add_argument("--lexical-only",action="store_true"); args=p.parse_args()
    if args.source == "gp":
        import os
        if not args.gp_dsn_env or not os.getenv(args.gp_dsn_env): raise SystemExit("--gp-dsn-env must name a populated environment variable")
        provider=PostgresProvider(os.environ[args.gp_dsn_env])
    else:
        if not args.duckdb: raise SystemExit("--duckdb is required for --source duckdb")
        provider=DuckDbProvider(args.duckdb)
    store=KbStore(provider); corpora=FIELDS if args.corpus=="all" else (args.corpus,)
    manifests=[]
    for corpus in corpora:
        fields,group,metadata_fields=FIELDS[corpus]; rows=store.corpus_frame(corpus)
        row_ids=[str(row.get("id") or "") for row in rows]
        if len(row_ids) != len(set(row_ids)) or any(not value for value in row_ids): raise ValueError(f"{corpus} contains duplicate or empty IDs")
        docs=prepare_from_frame(rows,text_fields=fields,group_field=group,metadata_fields=metadata_fields); vectors={}
        if corpus != "columns" and not args.lexical_only:
            encoded=make_bge_embedder(args.dense_model,device=args.device,cache_dir=args.model_cache_dir)([doc.text for doc in docs]); vectors={doc.id:[float(v) for v in encoded[pos]] for pos,doc in enumerate(docs)}
        manifests.append(build_index(args.index_root,corpus,docs,source_signature=store.current_source_signature(corpus),source_hash=store.rows_hash(rows),model_versions={"lexical":"bm25s","dense":args.dense_model if vectors else None,"device":args.device},dropped_rows=len(rows)-len(docs),dense_vectors=vectors))
    print(json.dumps(manifests,ensure_ascii=False,indent=2)); return 0


if __name__=="__main__": raise SystemExit(main())
