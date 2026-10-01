"""Deterministic static validation and AST facts for generated SQL."""
from __future__ import annotations

import re
from typing import Any, Mapping

from lib.services.sql_dialects.sanitize import sanitize_sql

_WRITE_KEYS = {"insert", "update", "delete", "merge", "create", "drop", "alter", "truncate", "grant", "revoke", "command", "copy", "call", "use", "attach", "detach", "load", "install", "export"}


def parser_dialect(dialect: str) -> str:
    return "postgres" if str(dialect).lower() == "greenplum" else "spark"


def physical_tables(sql: str, *, dialect: str = "spark") -> list[str]:
    """Extract canonical physical table names without CTE aliases or rewriting SQL."""
    import sqlglot
    from sqlglot import exp

    result: list[str] = []
    seen: set[str] = set()
    for tree in sqlglot.parse(sql, read=parser_dialect(dialect)):
        if tree is None:
            continue
        cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
        for node in tree.find_all(exp.Table):
            if not node.db and not node.catalog and node.name.lower() in cte_names:
                continue
            name = _table_name(node)
            normalized = name.lower()
            if name and normalized not in seen:
                seen.add(normalized)
                result.append(name)
    return result


def validate_sql(sql: str, *, dialect: str = "spark", schema: Mapping[str, Mapping[str, str]] | None = None, generated: bool = True) -> dict[str, Any]:
    cleaned = sanitize_sql(sql, dialect=dialect) if generated else (sql if isinstance(sql, str) else str(sql or ""))
    result: dict[str, Any] = {"status": "invalid", "valid": False, "dialect": dialect, "sql": cleaned, "issues": [], "warnings": []}
    if not cleaned:
        result["issues"].append({"code": "empty_sql", "message": "SQL is empty"})
        return result
    try:
        import sqlglot
        from sqlglot import exp
        expressions = sqlglot.parse(cleaned, read=parser_dialect(dialect))
    except Exception as exc:
        result["issues"].append({"code": "parse_error", "message": str(exc)})
        return result
    expressions = [expr for expr in expressions if expr is not None]
    if len(expressions) != 1:
        result["issues"].append({"code": "multiple_statements", "message": "Exactly one SQL statement is required"})
        return result
    tree = expressions[0]
    if generated:
        if any(str(getattr(node, "key", "")).lower() in _WRITE_KEYS for node in tree.walk()) or (not isinstance(tree, (exp.Select, exp.Union, exp.Subquery)) and tree.find(exp.Select) is None):
            result["issues"].append({"code": "not_read_only", "message": "Generated SQL must be a read-only SELECT/WITH statement"})
            return result
    known = _normalize_schema(schema or {})
    table_nodes = list(tree.find_all(exp.Table))
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    physical_table_nodes = [
        node for node in table_nodes
        if node.db or node.catalog or node.name.lower() not in cte_names
    ]
    source_tables = [_table_name(node) for node in physical_table_nodes]
    if known:
        unknown = [name for name in source_tables if _match_table(name, known) is None]
        if unknown:
            target = result["issues"] if generated else result["warnings"]
            target.append({"code": "unknown_table", "tables": sorted(set(unknown)), "message": "Physical tables are unknown to the selected KB grounding"})
        aliases: dict[str, str] = {}
        for node in physical_table_nodes:
            full = _table_name(node)
            matched = _match_table(full, known)
            if matched:
                aliases[node.alias_or_name.lower()] = matched
                aliases[node.name.lower()] = matched
        for col in tree.find_all(exp.Column):
            name = col.name
            qualifier = col.table.lower() if col.table else ""
            if qualifier and qualifier in aliases:
                table = aliases[qualifier]
                if name.lower() not in known[table]:
                    result["issues"].append({"code": "unknown_column", "column": col.sql(), "table": table, "message": f"Unknown column {name} in {table}"})
            elif not qualifier:
                query_tables = [matched for source in source_tables if (matched := _match_table(source, known))]
                candidates = [table for table in query_tables if name.lower() in known[table]]
                if len(candidates) == 0 and source_tables:
                    result["issues"].append({"code": "unknown_column", "column": name, "message": f"Unknown column {name}"})
                elif len(candidates) > 1 and len(source_tables) > 1:
                    result["issues"].append({"code": "ambiguous_column", "column": name, "tables": candidates, "message": f"Ambiguous unqualified column {name}"})
        if not unknown:
            try:
                from sqlglot.optimizer.qualify import qualify
                qualify(tree.copy(), dialect=parser_dialect(dialect), schema=known, validate_qualify_columns=True)
            except Exception as exc:
                message = str(exc)
                code = "ambiguous_column" if "ambiguous" in message.lower() else "unknown_column"
                if not any(issue.get("code") == code and issue.get("message") == message for issue in result["issues"]):
                    result["issues"].append({"code": code, "message": message})
    result["valid"] = not result["issues"]
    result["status"] = "valid" if result["valid"] else "invalid"
    result["ast"] = tree.sql(dialect=parser_dialect(dialect), pretty=False)
    return result


def sql_facts(sql: str, *, dialect: str = "spark", schema: Mapping[str, Mapping[str, str]] | None = None, descriptions: Mapping[str, Any] | None = None) -> dict[str, Any]:
    import sqlglot
    from sqlglot import exp
    cleaned = sanitize_sql(sql, dialect=dialect)
    tree = sqlglot.parse_one(cleaned, read=parser_dialect(dialect))
    tables = []
    aliases: dict[str, str] = {}
    ctes = [cte.alias_or_name for cte in tree.find_all(exp.CTE)]
    cte_set = {c.lower() for c in ctes}
    for node in tree.find_all(exp.Table):
        name = _table_name(node)
        if node.name.lower() in cte_set:
            continue
        if name not in tables:
            tables.append(name)
        aliases[node.alias_or_name] = name
    columns_by_table: dict[str, list[str]] = {}
    for col in tree.find_all(exp.Column):
        key = aliases.get(col.table, col.table or "unqualified")
        columns_by_table.setdefault(key, [])
        if col.name not in columns_by_table[key]:
            columns_by_table[key].append(col.name)
    joins = []
    for join in tree.find_all(exp.Join):
        joins.append({"source": join.this.sql(dialect=parser_dialect(dialect)), "kind": str(join.args.get("kind") or "INNER").upper(), "on": join.args.get("on").sql(dialect=parser_dialect(dialect)) if join.args.get("on") else None})
    where = tree.args.get("where")
    having = tree.args.get("having")
    group = tree.args.get("group")
    order = tree.args.get("order")
    limit = tree.args.get("limit")
    known = _normalize_schema(schema or {})
    unknown_tables = [name for name in tables if known and _match_table(name, known) is None]
    unknown_columns: list[str] = []
    if known:
        for owner, columns in columns_by_table.items():
            matched = _match_table(owner, known)
            if matched:
                unknown_columns.extend(f"{owner}.{col}" for col in columns if col.lower() not in known[matched])
    aggregations = [node.sql(dialect=parser_dialect(dialect)) for node in tree.find_all(exp.AggFunc)]
    windows = [node.sql(dialect=parser_dialect(dialect)) for node in tree.find_all(exp.Window)]
    return {"status": "ok", "dialect": dialect, "tables": tables, "ctes": ctes, "aliases": aliases, "columns_by_table": columns_by_table, "joins": joins, "join_keys": [j["on"] for j in joins if j["on"]], "filters": {"where": where.this.sql(dialect=parser_dialect(dialect)) if where else None, "having": having.this.sql(dialect=parser_dialect(dialect)) if having else None, "join": [j["on"] for j in joins if j["on"]]}, "aggregations": aggregations, "group_by": [x.sql(dialect=parser_dialect(dialect)) for x in group.expressions] if group else [], "windows": windows, "order_by": [x.sql(dialect=parser_dialect(dialect)) for x in order.expressions] if order else [], "limit": limit.expression.sql() if limit and limit.expression else None, "unknown_tables": sorted(set(unknown_tables)), "unknown_columns": sorted(set(unknown_columns)), "kb_descriptions": dict(descriptions or {}), "literal_values_verified": False}


def _normalize_schema(schema: Mapping[str, Mapping[str, str]]) -> dict[str, dict[str, str]]:
    return {str(table).lower(): {str(col).lower(): str(kind) for col, kind in columns.items()} for table, columns in schema.items()}


def _match_table(value: str, schema: Mapping[str, Any]) -> str | None:
    candidate = value.replace('"', "").replace("`", "").lower()
    for known in schema:
        if candidate == known or candidate.endswith("." + known) or known.endswith("." + candidate):
            return known
    return None


def _table_name(node: Any) -> str:
    parts = [getattr(node, key, "") for key in ("catalog", "db", "name")]
    return ".".join(str(part) for part in parts if part)
