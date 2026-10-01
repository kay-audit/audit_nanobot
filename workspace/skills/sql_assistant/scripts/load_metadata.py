"""Load real table/column metadata from explicit CSV or JSONL sources."""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from workspace.skills.sql_assistant.scripts._pg_admin import connect
from workspace.skills.sql_assistant.scripts._offline import quote_table

FIELDS={"tables":("id","table_name","group_key","layer","description","columns_summary","row_count","dialect"),"columns":("id","table_id","column_name","data_type","description","ordinal")}


def records(path: Path):
    if path.suffix.lower()==".jsonl":
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip(): yield json.loads(line)
    else:
        with path.open(encoding="utf-8-sig",newline="") as fh: yield from csv.DictReader(fh)


def main():
    p=argparse.ArgumentParser(); p.add_argument("--kind",choices=("tables","columns"),required=True); p.add_argument("--input",required=True); p.add_argument("--target-table"); p.add_argument("--dsn-env",default="DATABASE_URL"); p.add_argument("--dry-run",action="store_true"); args=p.parse_args()
    args.target_table=args.target_table or f"sqlagent.kb_{args.kind}"; table=quote_table(args.target_table); fields=FIELDS[args.kind]; stats={"read":0,"inserted":0,"updated":0,"invalid":0}; seen=set()
    with connect(args) as conn:
        for row in records(Path(args.input)):
            stats["read"]+=1; identity=row.get("id")
            if identity in (None,"") or identity in seen or any(row.get(f) in (None,"") for f in (("table_name",) if args.kind=="tables" else ("table_id","column_name"))): stats["invalid"]+=1; continue
            seen.add(identity)
            if args.dry_run: continue
            values=[row.get(f) for f in fields]; now=datetime.now(timezone.utc)
            assignments=", ".join(f'"{f}"=%s' for f in fields[1:]) + ", updated_at=%s"
            with conn.cursor() as cur:
                cur.execute(f"UPDATE {table} SET {assignments} WHERE id=%s",values[1:]+[now,identity])
                if cur.rowcount: stats["updated"]+=1
                else:
                    names=", ".join(f'"{f}"' for f in fields)+", updated_at"; marks=", ".join(["%s"]*(len(fields)+1))
                    cur.execute(f"INSERT INTO {table} ({names}) VALUES ({marks})",values+[now]); stats["inserted"]+=1
        if args.dry_run: conn.rollback()
    print(json.dumps(stats)); return 0 if not stats["invalid"] else 2
if __name__=="__main__": raise SystemExit(main())

