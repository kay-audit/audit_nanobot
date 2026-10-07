"""Idempotent offline migration from the legacy corporate script catalog."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone

from workspace.skills.sql_assistant.scripts._offline import quote_table
from lib.services.sql_static import physical_tables
from workspace.skills.sql_assistant.scripts._pg_admin import add_connection_arguments, connect

DEFAULT_SOURCE = "s_grnplm_ld_audit_da_project_34.70_aam_scripts_examples"


def extract_tables_json(sql: object, *, dialect: str) -> tuple[str | None, bool]:
    """Return canonical JSON physical-table names; preserve rows on parse error."""
    try:
        tables = physical_tables(sql if isinstance(sql, str) else str(sql or ""), dialect=dialect)
    except Exception:
        return None, True
    return json.dumps(tables, ensure_ascii=False, separators=(",", ":")), False


def main() -> int:
    p=argparse.ArgumentParser(); add_connection_arguments(p); p.add_argument("--source-table",default=DEFAULT_SOURCE); p.add_argument("--target-table",default="s_grnplm_ld_audit_da_project_34.kb_examples"); p.add_argument("--dialect",default="spark"); p.add_argument("--batch-size",type=int,default=500); p.add_argument("--dry-run",action="store_true"); args=p.parse_args()
    source,target=quote_table(args.source_table),quote_table(args.target_table)
    stats=Counter(read=0,inserted=0,updated=0,null_ids=0,duplicates=0,table_parse_errors=0)
    with connect(args) as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT script_id, km_id, file_name, file_path, script_summary, script_body FROM {source} ORDER BY script_id")
            seen=set()
            while True:
                batch=cur.fetchmany(max(1,args.batch_size))
                if not batch: break
                for script_id,km_id,file_name,file_path,description,sql in batch:
                    stats["read"]+=1
                    if script_id is None: stats["null_ids"]+=1; continue
                    if script_id in seen: stats["duplicates"]+=1; continue
                    seen.add(script_id)
                    tables_json,parse_error=extract_tables_json(sql,dialect=args.dialect)
                    if parse_error: stats["table_parse_errors"]+=1
                    if args.dry_run: continue
                    now=datetime.now(timezone.utc)
                    with conn.cursor() as write:
                        write.execute(f"UPDATE {target} SET km_id=%s,file_name=%s,file_path=%s,sql=%s,script_description=%s,tables=COALESCE(%s,tables),dialect=%s,updated_at=%s WHERE id=%s",(km_id,file_name,file_path,sql,description,tables_json,args.dialect,now,script_id))
                        if write.rowcount: stats["updated"]+=1
                        else:
                            write.execute(f"INSERT INTO {target} (id,script_id,km_id,file_name,file_path,nl,nl_variants,sql,script_description,tables,dialect,updated_at) VALUES (%s,%s,%s,%s,%s,NULL,NULL,%s,%s,%s,%s,%s)",(script_id,script_id,km_id,file_name,file_path,sql,description,tables_json,args.dialect,now)); stats["inserted"]+=1
        if args.dry_run: conn.rollback()
    print(" ".join(f"{k}={v}" for k,v in sorted(stats.items())))
    if stats["null_ids"] or stats["duplicates"]: return 2
    return 0


if __name__=="__main__": raise SystemExit(main())
