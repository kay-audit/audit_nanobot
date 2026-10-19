"""Resolve only the current user's active schema subscriptions, without caching identity."""
from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from lib.services.sql_static import parser_dialect

SOURCE_SCHEMA = "s_grnplm_ld_audit_da_sandbox_oarb"
_IDENTIFIER = re.compile(r"[a-zA-Z_][a-zA-Z_0-9]*\Z")
_LOGIN = re.compile(r"[0-9]{1,32}\Z")


def current_sudir_login() -> str | None:
    """Accept only the trusted Postgres RequestContext, never text or chat identity."""
    try:
        from nanobot.agent.tools.context import current_request_context
        ctx = current_request_context()
        value = getattr(ctx, "sender_id", None)
        if getattr(ctx, "channel", None) == "postgres" and isinstance(value, str) and _LOGIN.fullmatch(value):
            return value
    except Exception:
        pass
    return None


def _fetch(sql: str, *params: Any) -> list[dict[str, Any]]:
    from workspace.utils.db import fetch
    return fetch(sql, *params)


@dataclass
class SubscriptionResolution:
    sources: list[dict[str, Any]] = field(default_factory=list)
    public_to_physical: dict[str, str] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return bool(self.sources) and all(source["status"] == "subscribed" for source in self.sources)

    def report(self) -> dict[str, Any]:
        return {"status": "subscribed" if self.allowed else "subscription_blocked", "sources": self.sources}

    def public_schema(self, physical: Mapping[str, Mapping[str, str]]) -> dict[str, dict[str, str]]:
        normalized = {key.lower(): value for key, value in physical.items()}
        return {public: dict(normalized[source]) for public, source in self.public_to_physical.items()}

    def rewrite(self, sql: str, *, dialect: str) -> str:
        """Rewrite scoped external AST sources and qualified columns; reject other sources."""
        import sqlglot
        from sqlglot import exp
        from sqlglot.optimizer.scope import traverse_scope

        trees = [tree for tree in sqlglot.parse(sql, read=parser_dialect(dialect)) if tree is not None]
        if len(trees) != 1:
            raise ValueError("Exactly one statement is required")
        tree = trees[0]
        forward = {physical: public for public, physical in self.public_to_physical.items()}
        for scope in traverse_scope(tree):
            changed = {}
            for _, source in scope.selected_sources.values():
                if not isinstance(source, exp.Table):
                    continue
                if not isinstance(source.this, exp.Identifier) or source.catalog:
                    raise ValueError("Unsupported external source")
                name = f"{source.db}.{source.name}".lower()
                public = forward.get(name, name if name in self.public_to_physical else None)
                if public is None:
                    raise ValueError("External source is outside the authorized KB grounding")
                schema, table = public.split(".")
                changed[(source.db.lower(), source.name.lower())] = (schema, table)
                source.set("db", exp.to_identifier(schema))
                source.set("this", exp.to_identifier(table))
            for column in scope.columns:
                replacement = changed.get((column.db.lower(), column.table.lower()))
                if column.db and replacement:
                    column.set("db", exp.to_identifier(replacement[0]))
                    column.set("table", exp.to_identifier(replacement[1]))
        return tree.sql(dialect=parser_dialect(dialect))


class SubscriptionResolver:
    """Parameterized per-request lookups, scoped to one trusted login and required schemas."""

    def __init__(self, *, identity_confirmed: bool = False, catalog_mapping_confirmed: bool = True,
                 fetch: Callable[..., list[dict[str, Any]]] = _fetch,
                 identity: Callable[[], str | None] = current_sudir_login) -> None:
        self.identity_confirmed = identity_confirmed is True
        self.catalog_mapping_confirmed = catalog_mapping_confirmed is True
        self.fetch = fetch
        self.identity = identity

    async def resolve(self, table_names: Iterable[str]) -> SubscriptionResolution:
        names = sorted(set(str(name).lower() for name in table_names))
        sources = [{"table": name, "status": "subscription_mapping_unknown"} for name in names]
        resolution = SubscriptionResolution(sources)
        login = self.identity() if self.identity_confirmed else None
        if not isinstance(login, str) or not _LOGIN.fullmatch(login):
            for source in sources:
                source["status"] = "identity_unavailable"
            return resolution
        schemas = sorted({name.split(".")[0] for name in names if len(name.split(".")) == 2
                          and all(_IDENTIFIER.fullmatch(part) for part in name.split("."))})
        if not schemas:
            return resolution
        try:
            rows = await asyncio.to_thread(self.fetch,
                f"SELECT schema_name, sub_alias FROM {SOURCE_SCHEMA}.dvb_kav_user_subs "
                "WHERE sudir_login = %s AND lower(schema_name) = ANY(%s)", login, schemas)
            aliases: dict[str, set[str]] = {}
            invalid_schemas = set()
            for row in rows:
                schema = str(row["schema_name"]).lower()
                alias = str(row["sub_alias"] or "").lower()
                if schema not in schemas:
                    continue
                if not _IDENTIFIER.fullmatch(alias):
                    invalid_schemas.add(schema)
                else:
                    aliases.setdefault(schema, set()).add(alias)
        except Exception:
            for source in sources:
                source["status"] = "subscription_check_error"
            return resolution
        missing = [schema for schema in schemas if not aliases.get(schema) and schema not in invalid_schemas]
        catalog: dict[str, list[dict[str, str]]] = {}
        catalog_status = "subscription_mapping_unknown"
        if missing and self.catalog_mapping_confirmed:
            try:
                rows = await asyncio.to_thread(self.fetch,
                    f"SELECT meta_code, sub_alias, sub_name FROM {SOURCE_SCHEMA}.dvb_kav_subs_list "
                    "WHERE lower(meta_code) = ANY(%s)", missing)
                for row in rows:
                    schema = str(row["meta_code"]).lower()
                    if schema in missing and row.get("sub_alias") and row.get("sub_name"):
                        option = {"sub_alias": str(row["sub_alias"]), "sub_name": str(row["sub_name"])}
                        if option not in catalog.setdefault(schema, []):
                            catalog[schema].append(option)
            except Exception:
                catalog_status = "catalog_unavailable"
        for source in sources:
            name = source["table"]
            if len(name.split(".")) != 2 or not all(_IDENTIFIER.fullmatch(part) for part in name.split(".")):
                continue
            schema, table = name.split(".")
            choices = sorted(aliases.get(schema, ()))
            if choices:
                public = f"prx_{choices[0]}_{schema}.{table}"
                source.update(status="subscribed", public_table=public, active_aliases=choices)
                resolution.public_to_physical[public] = name
            elif schema in invalid_schemas:
                source["status"] = "subscription_mapping_unknown"
            elif catalog.get(schema):
                source.update(status="not_subscribed", options=sorted(catalog[schema], key=lambda row: (row["sub_alias"], row["sub_name"])))
            else:
                source["status"] = catalog_status
        public_sources: dict[str, list[dict[str, Any]]] = {}
        for source in sources:
            if source.get("public_table"):
                public_sources.setdefault(source["public_table"], []).append(source)
        for public, matches in public_sources.items():
            if len(matches) > 1:
                resolution.public_to_physical.pop(public, None)
                for source in matches:
                    source["status"] = "subscription_mapping_unknown"
                    source.pop("public_table", None)
        return resolution


def configured_resolver() -> SubscriptionResolver:
    try:
        from config import SETTINGS
        cfg = SETTINGS.get("gateway", {}).get("sql_subscriptions", {})
    except Exception:
        cfg = {}
    return SubscriptionResolver(identity_confirmed=cfg.get("identity_confirmed", False),
                                catalog_mapping_confirmed=cfg.get("catalog_mapping_confirmed", True))
