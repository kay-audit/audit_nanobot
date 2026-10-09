"""Deterministic static validation and AST facts for generated SQL."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from lib.services.sql_dialects.sanitize import sanitize_sql

_WRITE_KEYS = {"insert", "update", "delete", "merge", "create", "drop", "alter", "truncate", "grant", "revoke", "command", "copy", "call", "use", "attach", "detach", "load", "install", "export"}
_DEPENDENCY_WORD = re.compile(r"[\w$]+")
_DEPENDENCY_DOLLAR = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$")


def parser_dialect(dialect: str) -> str:
    return "postgres" if str(dialect).lower() == "greenplum" else "spark"


def physical_tables(sql: str, *, dialect: str = "spark") -> list[str]:
    """Extract external read dependencies statement-by-statement, without rewriting SQL.

    DDL targets and previously produced tables are not dependencies. Unsupported
    Hive/Spark syntax falls back to scoped FROM/JOIN extraction, not all names.
    """
    result: list[str] = []
    seen: set[str] = set()
    local_tables: set[str] = set()
    local_basenames: set[str] = set()
    default_schema = ""
    statements: list[list[_DependencyToken]] = [[]]
    for token in _dependency_tokens(sql):
        if token.value == ";" and token.kind == "symbol":
            statements.append([])
        else:
            statements[-1].append(token)
    for tokens in statements:
        if not tokens:
            continue
        pairs = _dependency_parentheses(tokens)
        command = _dependency_keyword(tokens[0])
        if command == "USE":
            index = 2 if len(tokens) > 1 and _dependency_keyword(tokens[1]) in {"DATABASE", "SCHEMA"} else 1
            default_schema, _ = _dependency_name(tokens, index)
            continue
        targets, query_start = _dependency_targets(tokens, pairs)
        current_targets = {name.casefold() for name in targets}
        current_basenames = {name.rsplit(".", 1)[-1] for name in current_targets}
        unqualified_targets = {name for name in current_targets if "." not in name}
        if command in {"DROP", "ALTER", "TRUNCATE", "SET", "DESCRIBE", "SHOW", "EXPLAIN", "MSCK", "REFRESH"}:
            continue
        query_tokens = tokens[query_start:] if query_start is not None else []
        if query_tokens:
            query_sql = sql[query_tokens[0].start:query_tokens[-1].end]
            sources = _dependency_ast_sources(query_sql, dialect)
            if sources is None:
                sources = _dependency_fallback_sources(query_tokens)
        else:
            sources = []
        for name in sources:
            normalized = name.casefold()
            basename = normalized.rsplit(".", 1)[-1]
            resolved = f"{default_schema.casefold()}.{normalized}" if default_schema and "." not in name else normalized
            if normalized in current_targets or resolved in current_targets or basename in unqualified_targets:
                continue
            if "." not in name and basename in current_basenames:
                continue
            if normalized in local_tables or resolved in local_tables or basename in local_basenames:
                continue
            if "." not in name and any(value.rsplit(".", 1)[-1] == basename for value in local_tables):
                continue
            if name and normalized not in seen:
                seen.add(normalized)
                result.append(name)
        for name in targets:
            normalized = name.casefold()
            if "." not in name and default_schema:
                local_tables.add(f"{default_schema.casefold()}.{normalized}")
            else:
                local_tables.add(normalized)
                if "." not in name:
                    local_basenames.add(normalized)
    return result


@dataclass(frozen=True)
class _DependencyToken:
    kind: str
    value: str
    start: int
    end: int


def _dependency_tokens(sql: str) -> list[_DependencyToken]:
    tokens = []
    index = 0
    while index < len(sql):
        start = index
        char = sql[index]
        if char.isspace():
            index += 1
            continue
        if sql.startswith("--", index):
            newline = sql.find("\n", index)
            index = len(sql) if newline < 0 else newline + 1
            continue
        if sql.startswith("/*", index):
            index += 2
            depth = 1
            while index < len(sql) and depth:
                if sql.startswith("/*", index):
                    depth += 1
                    index += 2
                elif sql.startswith("*/", index):
                    depth -= 1
                    index += 2
                else:
                    index += 1
            if depth:
                raise ValueError("Unterminated SQL block comment")
            continue
        if char in {"'", '"', "`"}:
            index += 1
            value = []
            while index < len(sql):
                if sql[index] == char:
                    if index + 1 < len(sql) and sql[index + 1] == char:
                        value.append(char)
                        index += 2
                        continue
                    index += 1
                    break
                if sql[index] == "\\" and index + 1 < len(sql):
                    value.append(sql[index + 1])
                    index += 2
                else:
                    value.append(sql[index])
                    index += 1
            else:
                raise ValueError("Unterminated SQL quoted token")
            tokens.append(_DependencyToken("literal" if char == "'" else "quoted", "" if char == "'" else "".join(value), start, index))
            continue
        dollar = _DEPENDENCY_DOLLAR.match(sql, index) if char == "$" else None
        if dollar:
            delimiter = dollar.group()
            end = sql.find(delimiter, index + len(delimiter))
            if end < 0:
                raise ValueError("Unterminated SQL dollar-quoted literal")
            index = end + len(delimiter)
            tokens.append(_DependencyToken("literal", "", start, index))
            continue
        word = _DEPENDENCY_WORD.match(sql, index)
        if word:
            index += len(word.group())
            tokens.append(_DependencyToken("word", word.group(), start, index))
        else:
            index += 1
            tokens.append(_DependencyToken("symbol", char, start, index))
    return tokens


def _dependency_keyword(token: _DependencyToken) -> str:
    return token.value.upper() if token.kind == "word" else ""


def _dependency_name(tokens: list[_DependencyToken], index: int) -> tuple[str, int]:
    parts = []
    while index < len(tokens) and tokens[index].kind in {"word", "quoted"}:
        parts.append(tokens[index].value)
        index += 1
        if index >= len(tokens) or tokens[index].value != ".":
            break
        index += 1
        if index >= len(tokens) or tokens[index].kind not in {"word", "quoted"}:
            return "", index
    return ".".join(parts), index


def _dependency_parentheses(tokens: list[_DependencyToken]) -> dict[int, int]:
    pairs, stack = {}, []
    for index, token in enumerate(tokens):
        if token.kind != "symbol":
            continue
        if token.value == "(":
            stack.append(index)
        elif token.value == ")":
            if not stack:
                raise ValueError("Unbalanced SQL parentheses")
            pairs[stack.pop()] = index
    if stack:
        raise ValueError("Unbalanced SQL parentheses")
    return pairs


def _dependency_targets(tokens: list[_DependencyToken], pairs: Mapping[int, int]) -> tuple[list[str], int | None]:
    targets = []
    command = _dependency_keyword(tokens[0])
    index = 0
    while index < len(tokens):
        keyword = _dependency_keyword(tokens[index])
        if tokens[index].value == "(" and index in pairs:
            index = pairs[index] + 1
            continue
        if command == "CREATE" and keyword in {"TABLE", "VIEW"}:
            start = index + 1
            if start < len(tokens) and _dependency_keyword(tokens[start]) == "IF":
                start += 3
            name, _ = _dependency_name(tokens, start)
            if name:
                targets.append(name)
            index = start
            continue
        if keyword == "INSERT":
            start = index + 1
            while start < len(tokens) and _dependency_keyword(tokens[start]) in {"INTO", "OVERWRITE", "TABLE"}:
                start += 1
            if start < len(tokens) and _dependency_keyword(tokens[start]) not in {"DIRECTORY", "LOCAL"}:
                name, _ = _dependency_name(tokens, start)
                if name:
                    targets.append(name)
        if command == "CREATE" and keyword == "AS" and index + 1 < len(tokens):
            if _dependency_keyword(tokens[index + 1]) in {"SELECT", "WITH"} or tokens[index + 1].value == "(":
                return targets, index + 1
        index += 1
    return targets, None if command == "CREATE" else 0


def _dependency_ast_sources(sql: str, dialect: str) -> list[str] | None:
    try:
        import sqlglot
        from sqlglot import exp
        from sqlglot.optimizer.scope import traverse_scope

        tree = sqlglot.parse_one(sql, read=parser_dialect(dialect))
        scopes = traverse_scope(tree)
        if not scopes:
            return None
        result = []
        for scope in scopes:
            for _node, source in scope.selected_sources.values():
                if isinstance(source, exp.Table) and isinstance(source.this, exp.Identifier):
                    result.append(_table_name(source))
        return result
    except Exception:
        return None


def _dependency_fallback_sources(tokens: list[_DependencyToken], inherited: frozenset[str] = frozenset()) -> list[str]:
    if not tokens:
        return []
    pairs = _dependency_parentheses(tokens)
    aliases = set(inherited)
    ctes = []
    index = 0
    if _dependency_keyword(tokens[0]) == "WITH":
        index = 2 if len(tokens) > 1 and _dependency_keyword(tokens[1]) == "RECURSIVE" else 1
        while index < len(tokens):
            name, after = _dependency_name(tokens, index)
            if not name:
                break
            if after in pairs:
                after = pairs[after] + 1
            if after >= len(tokens) or _dependency_keyword(tokens[after]) != "AS":
                break
            after += 1
            while after < len(tokens) and _dependency_keyword(tokens[after]) in {"NOT", "MATERIALIZED"}:
                after += 1
            if after not in pairs:
                break
            aliases.add(name.casefold())
            ctes.append(tokens[after + 1:pairs[after]])
            index = pairs[after] + 1
            if index >= len(tokens) or tokens[index].value != ",":
                break
            index += 1
    result = []
    for body in ctes:
        result.extend(_dependency_fallback_sources(body, frozenset(aliases)))
    query_seen = index < len(tokens) and _dependency_keyword(tokens[index]) == "FROM"
    in_from = False
    boundaries = {"WHERE", "GROUP", "HAVING", "QUALIFY", "ORDER", "LIMIT", "UNION", "EXCEPT", "INTERSECT", "WINDOW", "ON", "INSERT"}
    while index < len(tokens):
        token = tokens[index]
        keyword = _dependency_keyword(token)
        if index in pairs:
            result.extend(_dependency_fallback_sources(tokens[index + 1:pairs[index]], frozenset(aliases)))
            index = pairs[index] + 1
            continue
        if keyword == "SELECT":
            query_seen = True
            in_from = False
        if keyword in boundaries:
            in_from = False
        relation = query_seen and (keyword in {"FROM", "JOIN"} or (in_from and token.value == ","))
        if relation:
            if keyword == "FROM":
                in_from = True
            start = index + 1
            if start < len(tokens) and _dependency_keyword(tokens[start]) == "LATERAL":
                start += 1
            name, after = _dependency_name(tokens, start)
            if name and _dependency_keyword(tokens[start]) not in {"SELECT", "WITH", "VALUES", "UNNEST"}:
                if after not in pairs and ("." in name or name.casefold() not in aliases):
                    result.append(name)
        index += 1
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
    cte_names = {cte.alias_or_name.casefold() for cte in tree.find_all(exp.CTE)}
    physical_table_nodes = [
        node for node in table_nodes
        if node.db or node.catalog or node.name.casefold() not in cte_names
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
                aliases[node.alias_or_name.casefold()] = matched
                aliases[node.name.casefold()] = matched
        for col in tree.find_all(exp.Column):
            name = col.name
            qualifier = col.table.casefold() if col.table else ""
            if qualifier and qualifier in aliases:
                table = aliases[qualifier]
                if name.casefold() not in known[table]:
                    result["issues"].append({"code": "unknown_column", "column": col.sql(), "table": table, "message": f"Unknown column {name} in {table}"})
            elif not qualifier:
                query_tables = [matched for source in source_tables if (matched := _match_table(source, known))]
                candidates = [table for table in query_tables if name.casefold() in known[table]]
                if len(candidates) == 0 and source_tables:
                    result["issues"].append({"code": "unknown_column", "column": name, "message": f"Unknown column {name}"})
                elif len(candidates) > 1 and len(source_tables) > 1:
                    result["issues"].append({"code": "ambiguous_column", "column": name, "tables": candidates, "message": f"Ambiguous unqualified column {name}"})
        if not unknown:
            try:
                from sqlglot.optimizer.qualify import qualify
                from sqlglot.schema import MappingSchema
                qualification_tree = tree.copy()
                if parser_dialect(dialect) == "spark":
                    for identifier in qualification_tree.find_all(exp.Identifier):
                        identifier.set("this", identifier.name.casefold())
                    qualification_schema = MappingSchema(_qualification_schema(known), dialect="spark", normalize=False)
                else:
                    qualification_schema = MappingSchema(_qualification_schema(schema or {}), dialect=parser_dialect(dialect), normalize=False)
                qualify(qualification_tree, dialect=parser_dialect(dialect), schema=qualification_schema, validate_qualify_columns=True)
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
                unknown_columns.extend(f"{owner}.{col}" for col in columns if col.casefold() not in known[matched])
    aggregations = [node.sql(dialect=parser_dialect(dialect)) for node in tree.find_all(exp.AggFunc)]
    windows = [node.sql(dialect=parser_dialect(dialect)) for node in tree.find_all(exp.Window)]
    return {"status": "ok", "dialect": dialect, "tables": tables, "ctes": ctes, "aliases": aliases, "columns_by_table": columns_by_table, "joins": joins, "join_keys": [j["on"] for j in joins if j["on"]], "filters": {"where": where.this.sql(dialect=parser_dialect(dialect)) if where else None, "having": having.this.sql(dialect=parser_dialect(dialect)) if having else None, "join": [j["on"] for j in joins if j["on"]]}, "aggregations": aggregations, "group_by": [x.sql(dialect=parser_dialect(dialect)) for x in group.expressions] if group else [], "windows": windows, "order_by": [x.sql(dialect=parser_dialect(dialect)) for x in order.expressions] if order else [], "limit": limit.expression.sql() if limit and limit.expression else None, "unknown_tables": sorted(set(unknown_tables)), "unknown_columns": sorted(set(unknown_columns)), "kb_descriptions": dict(descriptions or {}), "literal_values_verified": False}


def _normalize_schema(schema: Mapping[str, Mapping[str, str]]) -> dict[str, dict[str, str]]:
    return {str(table).casefold(): {str(col).casefold(): str(kind) for col, kind in columns.items()} for table, columns in schema.items()}


def _qualification_schema(schema: Mapping[str, Mapping[str, str]]) -> dict[str, Any]:
    nested: dict[str, Any] = {}
    for table, columns in schema.items():
        parts = str(table).split(".")
        target = nested
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = dict(columns)
    return nested


def _match_table(value: str, schema: Mapping[str, Any]) -> str | None:
    candidate = value.replace('"', "").replace("`", "").casefold()
    for known in schema:
        if candidate == known or candidate.endswith("." + known) or known.endswith("." + candidate):
            return known
    return None


def _table_name(node: Any) -> str:
    parts = [getattr(node, key, "") for key in ("catalog", "db", "name")]
    return ".".join(str(part) for part in parts if part)
