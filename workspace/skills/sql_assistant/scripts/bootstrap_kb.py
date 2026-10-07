"""Bootstrap independent legacy examples and physical metadata into SQL Assistant KB."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from lib.services.kb_store import KB_SCHEMA
from lib.services.sql_static import physical_tables
from workspace.skills.sql_assistant.scripts._offline import quote_table
from workspace.skills.sql_assistant.scripts._pg_admin import add_connection_arguments, connect
from workspace.skills.sql_assistant.scripts.migrate_legacy_examples import DEFAULT_SOURCE

DEFAULT_METADATA_TABLE = "s_grnplm_ld_audit_da_sandbox_oarb.dvb_kav_repl_test"


@dataclass
class BootstrapPlan:
    examples: list[dict[str, Any]]
    tables: list[dict[str, Any]]
    columns: list[dict[str, Any]]
    stats: dict[str, Any]


class SyntheticIdCollisionError(ValueError):
    """Two different logical keys cannot share a synthetic BIGINT."""


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _identity(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        result = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return result if -(2**63) <= result < 2**63 else None


def _synthetic_id(key: str) -> int:
    digest = hashlib.blake2b(key.encode("utf-8"), digest_size=8).digest()
    return (int.from_bytes(digest, "big") & ((1 << 63) - 1)) or 1


def _claim_id(key: str, logical_key: tuple[str, ...], keys: dict[int, tuple[str, ...]]) -> int:
    identity = _synthetic_id(key)
    previous = keys.get(identity)
    if previous is not None and previous != logical_key:
        raise SyntheticIdCollisionError(f"Synthetic BIGINT collision: id={identity}, keys={previous!r} and {logical_key!r}")
    keys[identity] = logical_key
    return identity


def _prepare_examples(examples: Iterable[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    examples = list(examples)
    ids = Counter(_identity(row.get("script_id")) for row in examples)
    result = []
    stats: dict[str, Any] = {
        "examples_read": len(examples), "examples_parse_errors": 0,
        "examples_invalid_ids": 0, "examples_duplicate_ids": 0, "examples_null_sql": 0,
    }
    for row in examples:
        sql = row.get("script_body")
        tables_json = None
        if isinstance(sql, str):
            try:
                tables_json = json.dumps(physical_tables(sql, dialect="spark"), ensure_ascii=False, separators=(",", ":"))
            except Exception:
                stats["examples_parse_errors"] += 1
        else:
            stats["examples_null_sql"] += 1
        script_id = _identity(row.get("script_id"))
        if script_id is None:
            stats["examples_invalid_ids"] += 1
            continue
        if ids[script_id] > 1:
            stats["examples_duplicate_ids"] += 1
            continue
        if not isinstance(sql, str):
            continue
        result.append({
            "id": script_id, "script_id": script_id,
            "km_id": row.get("km_id"), "file_name": row.get("file_name"),
            "file_path": row.get("file_path"), "sql": sql,
            "script_description": row.get("script_summary"),
            "tables": tables_json, "dialect": "spark",
        })
    stats["examples_to_upsert"] = len(result)
    return result, stats


def build_plan(examples: Iterable[Mapping[str, Any]], metadata: Iterable[Mapping[str, Any]], *, summary_columns: int = 50) -> BootstrapPlan:
    if summary_columns < 1:
        raise ValueError("summary_columns must be positive")
    example_rows, stats = _prepare_examples(examples)
    stats.update(metadata_rows_read=0, metadata_invalid_rows=0, duplicate_columns=0)
    grouped: dict[tuple[str, str], dict[tuple[str, str, str], dict[str, str]]] = defaultdict(dict)
    for row in metadata:
        stats["metadata_rows_read"] += 1
        names = tuple(_text(row.get(field)) for field in ("schema_name", "table_name", "field_name"))
        if not all(names):
            stats["metadata_invalid_rows"] += 1
            continue
        key = tuple(name.lower() for name in names)
        prepared = {
            "schema_name": names[0], "table_name": names[1], "field_name": names[2],
            "table_descr": _text(row.get("table_descr")),
            "field_descr": _text(row.get("field_descr")), "field_type": _text(row.get("field_type")),
        }
        columns = grouped[key[:2]]
        previous = columns.get(key)
        if previous is not None:
            stats["duplicate_columns"] += 1
            if any(previous[field] != prepared[field] for field in ("table_descr", "field_descr", "field_type")):
                raise ValueError(f"Conflicting duplicate metadata column: {key!r}")
            prepared = min(previous, prepared, key=lambda item: tuple(item.values()))
        columns[key] = prepared
    tables, columns, synthetic_keys = [], [], {}
    for table_key, fields in sorted(grouped.items()):
        ordered = [row for _key, row in sorted(fields.items())]
        names = min((row["schema_name"], row["table_name"]) for row in ordered)
        full_name = ".".join(names)
        table_id = _claim_id(".".join(table_key), ("table", *table_key), synthetic_keys)
        descriptions = {row["table_descr"] for row in ordered if row["table_descr"]}
        if len(descriptions) > 1:
            raise ValueError(f"Conflicting metadata table descriptions: {table_key!r}")
        table_columns = []
        for column_key, row in sorted(fields.items()):
            column_id = _claim_id(".".join(column_key), ("column", *column_key), synthetic_keys)
            table_columns.append({
                "id": column_id, "table_id": table_id, "column_name": row["field_name"],
                "data_type": row["field_type"] or None,
                "description": row["field_descr"] or None, "ordinal": None,
            })
        tables.append({
            "id": table_id, "table_name": full_name,
            "description": next(iter(descriptions), None), "dialect": "spark",
            "columns_summary": ", ".join(f"{row['column_name']}:{row['data_type'] or 'UNKNOWN'}" for row in table_columns[:summary_columns]),
        })
        columns.extend(table_columns)
    stats.update(unique_tables=len(tables), unique_columns=len(columns), tables_to_upsert=len(tables), columns_to_upsert=len(columns))
    return BootstrapPlan(example_rows, tables, columns, stats)


def _read(connection: Any, sql: str, params: list[Any] | None = None, *, batch_size: int) -> list[dict[str, Any]]:
    result = []
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        names = [column[0] for column in cursor.description]
        while batch := cursor.fetchmany(batch_size):
            result.extend(dict(zip(names, row)) for row in batch)
    return result

def _upsert(
    connection: Any,
    table: str,
    rows: Iterable[Mapping[str, Any]],
    *,
    preserve_tables: bool = False,
) -> dict[str, int]:
    rows = list(rows)

    if not rows:
        return {"inserted": 0, "updated": 0}

    from psycopg2.extras import execute_values

    fields = list(rows[0].keys())
    all_fields = fields + ["updated_at"]

    quoted_fields = ", ".join(f'"{field}"' for field in all_fields)
    temp_table = "tmp_sql_assistant_upsert"

    now = datetime.now(timezone.utc)

    values = [
        tuple(row[field] for field in fields) + (now,)
        for row in rows
    ]

    with connection.cursor() as cursor:
        cursor.execute(f"DROP TABLE IF EXISTS {temp_table}")

        cursor.execute(
            f"""
            CREATE TEMP TABLE {temp_table}
            (LIKE {table})
            DISTRIBUTED BY (id)
            """
        )

        execute_values(
            cursor,
            f"""
            INSERT INTO {temp_table} ({quoted_fields})
            VALUES %s
            """,
            values,
            page_size=2000,
        )

        update_fields = [field for field in fields if field != "id"]

        assignments = []

        for field in update_fields:
            if preserve_tables and field == "tables":
                assignments.append(
                    f'"{field}" = COALESCE(s."{field}", t."{field}")'
                )
            else:
                assignments.append(
                    f'"{field}" = s."{field}"'
                )

        assignments.append('"updated_at" = s."updated_at"')

        cursor.execute(
            f"""
            UPDATE {table} AS t
            SET {", ".join(assignments)}
            FROM {temp_table} AS s
            WHERE t.id = s.id
            """
        )

        updated = cursor.rowcount

        select_fields = ", ".join(
            f's."{field}"'
            for field in all_fields
        )

        cursor.execute(
            f"""
            INSERT INTO {table} ({quoted_fields})
            SELECT {select_fields}
            FROM {temp_table} AS s
            LEFT JOIN {table} AS t
                ON t.id = s.id
            WHERE t.id IS NULL
            """
        )

        inserted = cursor.rowcount

        cursor.execute(f"DROP TABLE {temp_table}")

    return {
        "inserted": inserted,
        "updated": updated,
    }


def bootstrap(connection: Any, *, source_table: str = DEFAULT_SOURCE, metadata_table: str = DEFAULT_METADATA_TABLE, target_kb_schema: str = KB_SCHEMA, dry_run: bool = False, batch_size: int = 500, summary_columns: int = 50) -> dict[str, Any]:
    if batch_size < 1 or summary_columns < 1:
        raise ValueError("batch_size and summary_columns must be positive")
    source, metadata_source = map(quote_table, (source_table, metadata_table))
    targets = {kind: quote_table(f"{target_kb_schema}.kb_{kind}") for kind in ("examples", "tables", "columns")}
    examples = _read(connection, f"SELECT script_id, km_id, file_path, file_name, script_body, script_summary FROM {source} ORDER BY script_id", batch_size=batch_size)
    metadata = _read(connection, f"SELECT schema_name, schema_descr, table_name, table_descr, field_name, field_descr, field_type FROM {metadata_source}", batch_size=batch_size)
    plan = build_plan(examples, metadata, summary_columns=summary_columns)
    plan.stats["dry_run"] = dry_run
    if not dry_run:
        plan.stats["upsert"] = {}

        for kind in ("examples", "tables", "columns"):
            rows = getattr(plan, kind)

            print(
                f"[bootstrap] Upsert {kind}: {len(rows)} rows...",
                flush=True,
            )

            result = _upsert(
                connection,
                targets[kind],
                rows,
                preserve_tables=kind == "examples",
            )

            plan.stats["upsert"][kind] = result

            print(
                f"[bootstrap] {kind}: "
                f"inserted={result['inserted']}, "
                f"updated={result['updated']}",
                flush=True,
            )
    return plan.stats


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--source-table", default=DEFAULT_SOURCE)
    result.add_argument("--metadata-table", default=DEFAULT_METADATA_TABLE)
    result.add_argument("--target-kb-schema", default=KB_SCHEMA)
    add_connection_arguments(result)
    result.add_argument("--batch-size", type=int, default=500)
    result.add_argument("--summary-columns", type=int, default=50)
    result.add_argument("--dry-run", action="store_true")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.batch_size < 1 or args.summary_columns < 1:
        parser().error("--batch-size and --summary-columns must be positive")
    with connect(args) as connection:
        if args.dry_run:
            connection.set_session(readonly=True)
        stats = bootstrap(connection, source_table=args.source_table, metadata_table=args.metadata_table, target_kb_schema=args.target_kb_schema, dry_run=args.dry_run, batch_size=args.batch_size, summary_columns=args.summary_columns)
        if args.dry_run:
            connection.rollback()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 2 if stats["examples_invalid_ids"] or stats["examples_duplicate_ids"] or stats["examples_null_sql"] or stats["metadata_invalid_rows"] or stats["duplicate_columns"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
