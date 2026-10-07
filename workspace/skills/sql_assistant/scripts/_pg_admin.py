from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from typing import Any, Callable

from workspace.skills.sql_assistant.scripts._offline import load_state, quote_table, save_state


def add_common(parser: argparse.ArgumentParser, *, table: str) -> None:
    add_connection_arguments(parser)
    parser.add_argument("--table", default=table)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--state-file")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")


def add_connection_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", choices=("prod", "test"))
    parser.add_argument("--dsn-env", help="Optional explicit legacy environment override")


def initialize_settings(args) -> None:
    from config import _initialize_settings, is_settings_initialized
    if not is_settings_initialized():
        profile = getattr(args, "profile", None)
        if not profile:
            raise ValueError("--profile prod|test is required to read project.json settings")
        _initialize_settings(profile)


def resolve_admin_dsn(args) -> str:
    env_name = getattr(args, "dsn_env", None)
    if env_name:
        dsn = os.environ.get(env_name, "")
        if not dsn or dsn.startswith("${"):
            raise ValueError("The explicitly requested DSN environment override is missing")
        return dsn
    initialize_settings(args)
    from workspace.utils.db import resolve_dsn
    dsn = resolve_dsn()
    if not isinstance(dsn, str) or not dsn.strip() or dsn.startswith("${"):
        raise ValueError("Configure channels.postgres.dsn in project.json")
    return dsn


def connect(args):
    dsn = resolve_admin_dsn(args)
    import psycopg2
    try:
        connection = psycopg2.connect(dsn, gssencmode="disable")
        if getattr(args, "dry_run", False):
            connection.set_session(readonly=True)
        return connection
    except Exception:
        raise RuntimeError("SQL Assistant database connection failed; verify project.json configuration") from None


def run_updates(args, *, select_columns: str, where: str, transform: Callable[[dict[str, Any]], dict[str, Any] | None]) -> dict[str, int]:
    table = quote_table(args.table); state = load_state(args.state_file); last_id = int(state.get("last_id", 0)); stats = {"read":0,"changed":0,"unchanged":0,"errors":0}
    with connect(args) as conn:
        while True:
            with conn.cursor() as cur:
                cur.execute(f"SELECT {select_columns} FROM {table} WHERE id > %s AND ({where}) ORDER BY id LIMIT %s", (last_id, max(1,args.batch_size)))
                cols=[d.name for d in cur.description]; rows=[dict(zip(cols,row)) for row in cur.fetchall()]
            if not rows: break
            for row in rows:
                last_id=int(row["id"]); stats["read"]+=1
                try: changes=transform(row)
                except Exception: stats["errors"]+=1; save_state(args.state_file,{"last_id":last_id,"stats":stats}); continue
                if not changes: stats["unchanged"]+=1; continue
                stats["changed"]+=1
                if not args.dry_run:
                    fields=list(changes); values=[changes[f] for f in fields]
                    assignments=", ".join(f'"{f}"=%s' for f in fields) + ', updated_at=%s'
                    with conn.cursor() as cur: cur.execute(f"UPDATE {table} SET {assignments} WHERE id=%s", values+[datetime.now(timezone.utc),row["id"]])
            if args.dry_run: conn.rollback()
            else: conn.commit()
            save_state(args.state_file,{"last_id":last_id,"stats":stats})
    return stats

