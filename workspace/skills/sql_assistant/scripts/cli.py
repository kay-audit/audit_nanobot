from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from lib.services.sql_assistant_runtime import SqlAssistantRuntime, structured_error
from lib.services.sql_static import sql_facts, validate_sql
from workspace.skills.sql_assistant.scripts._offline import DuckDbProvider, StaticProvider


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="SQL Assistant diagnostics (no autonomous agent loop)")
    p.add_argument("--mode", required=True, choices=("ready", "search", "describe", "validate", "generate", "facts", "smoke"))
    p.add_argument("--prompt", default="")
    p.add_argument("--dialect", choices=("spark", "greenplum"), default="spark")
    p.add_argument("--duckdb", help="Explicit published DuckDB snapshot path")
    p.add_argument("--index-root")
    p.add_argument("--corpus", choices=("tables", "examples", "columns"), default="tables")
    p.add_argument("--table-id", action="append", default=[])
    p.add_argument("--example-id", action="append", default=[])
    p.add_argument("--group-key", action="append", default=[])
    p.add_argument("--column-query", default="")
    p.add_argument("--sql")
    p.add_argument("--sql-file")
    p.add_argument("--live-analyze", action="store_true")
    return p


def _sql(args) -> str:
    if args.sql_file: return Path(args.sql_file).read_text(encoding="utf-8")
    return args.sql or ""


async def run(args) -> dict | str:
    provider = DuckDbProvider(args.duckdb) if args.duckdb else StaticProvider()
    runtime = SqlAssistantRuntime(provider, index_root=args.index_root)
    if args.mode == "ready": return runtime.ready(args.prompt)
    if args.mode == "search": return runtime.search(args.prompt, corpus=args.corpus)
    if args.mode == "describe": return await runtime.describe(table_ids=args.table_id, group_keys=args.group_key, example_ids=args.example_id, detail="full", column_query=args.column_query)
    if args.mode == "validate":
        return await runtime.validate(_sql(args), dialect=args.dialect, table_ids=args.table_id, live_analyze=args.live_analyze) if args.table_id else validate_sql(_sql(args), dialect=args.dialect)
    if args.mode == "facts": return runtime.facts(_sql(args), dialect=args.dialect, table_ids=args.table_id, example_ids=args.example_id) if args.table_id or args.example_id else sql_facts(_sql(args), dialect=args.dialect)
    if args.mode == "generate": return await runtime.generate(question=args.prompt, dialect=args.dialect, table_ids=args.table_id, example_ids=args.example_id, column_query=args.column_query, live_analyze=args.live_analyze)
    checks = {"static": validate_sql("SELECT 1", dialect=args.dialect)}
    if args.duckdb:
        checks["kb"] = {corpus: len(runtime.store.corpus_frame(corpus, max_rows=3)) for corpus in ("tables", "columns", "examples")}
    return {"status": "ok" if checks["static"]["valid"] else "error", "checks": checks}


def main() -> int:
    args = parser().parse_args()
    try: result = asyncio.run(run(args))
    except Exception as exc: result = structured_error(exc)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str) if not isinstance(result, str) else result)
    return 0 if not isinstance(result, dict) or result.get("status") not in {"error", "not_ready", "invalid"} else 1


if __name__ == "__main__": raise SystemExit(main())

