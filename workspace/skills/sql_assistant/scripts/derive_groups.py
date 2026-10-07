from __future__ import annotations
import argparse, json, re
from workspace.skills.sql_assistant.scripts._pg_admin import add_common, run_updates

def derive(name: str) -> str:
    value=str(name or "").lower().split(".")[-1]
    value=re.sub(r"^(?:prx|src|ods|dm|dwh|raw|stg)_[^_]+_", "", value)
    return re.sub(r"_(?:hist|archive|snapshot|v\d+)$", "", value)

def main():
    p=argparse.ArgumentParser(); add_common(p,table="s_grnplm_ld_audit_da_project_34.kb_tables"); args=p.parse_args()
    stats=run_updates(args,select_columns="id, table_name, group_key",where="TRUE",transform=lambda r: ({"group_key":derive(r["table_name"])} if (args.force or not r.get("group_key")) and r.get("group_key")!=derive(r["table_name"]) else None))
    print(json.dumps(stats)); return 0 if not stats["errors"] else 2
if __name__=="__main__": raise SystemExit(main())

