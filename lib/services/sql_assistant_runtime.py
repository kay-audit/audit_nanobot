"""Native SQL Assistant workflows shared by gateway tools and diagnostic CLI."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from lib.services.hybrid_search import HybridIndex, load_index, prepare_from_frame
from lib.services.kb_store import KbStore, KbStoreError
from lib.services.spark_backend import SparkBackend
from lib.services.sql_dialects.greenplum import generation_rules as greenplum_rules
from lib.services.sql_dialects.spark import generation_rules as spark_rules
from lib.services.sql_static import sql_facts, validate_sql

NOT_FOUND = "Подходящий готовый скрипт в базе не найден."
logger = logging.getLogger(__name__)
_SCRIPT_ID = re.compile(r"\bscript[\s_-]*id\s*[:=#№]?\s*([\w.-]+)\b", re.I)
_KM_ID = re.compile(r"\bkm[\s_-]*id\s*[:=#№]?\s*([\w.-]+)", re.I)
_FILE = re.compile(r"(?<![\w.-])([^\s\"'<>|/\\]+\.(?:sql|hql|ddl|txt|py))\b", re.I)
_PATH = re.compile(r"((?:[A-Za-z]:)?[/\\][^\s\"'<>|]+)")


def exact_hint(prompt: str) -> tuple[str, Any, bool] | None:
    if match := _SCRIPT_ID.search(prompt or ""):
        return "script_id", match.group(1), False
    if match := _KM_ID.search(prompt or ""):
        rest = _KM_ID.sub(" ", prompt.lower())
        rest = re.sub(r"\b(покажи|дай|найди|нужен|нужны|скрипт|скрипты|sql|для|по|все|готовый|готовые)\b", " ", rest)
        return "km_id", match.group(1), bool(re.search(r"[a-zа-яё0-9]{3,}", rest, re.I))
    if match := _PATH.search(prompt or ""):
        return "file_path", match.group(1), False
    if match := _FILE.search(prompt or ""):
        return "file_name", match.group(1), False
    return None


def markdown_fence(sql: str) -> str:
    longest = max((len(value) for value in re.findall(r"`+", sql)), default=0)
    fence = "`" * max(3, longest + 1)
    separator = "" if sql.endswith(("\n", "\r")) else "\n"
    return f"{fence}sql\n{sql}{separator}{fence}"


class SqlAssistantRuntime:
    def __init__(self, provider: Any, *, index_root: str | Path | None = None, score_floor: float = 0.4, embedder: Any = None, reranker: Any = None, model_key: str = "default") -> None:
        self.store = KbStore(provider)
        self.index_root = Path(index_root) if index_root else None
        self.score_floor = float(score_floor)
        self.embedder = embedder
        self.reranker = reranker
        self.model_key = model_key

    def ready(self, prompt: str) -> dict[str, Any]:
        hint = exact_hint(prompt)
        if not hint:
            return {"status": "requires_search", "message": "No exact identifier was supplied; use kb_search(corpus=examples) first."}
        kind, value, has_detail = hint
        rows = self.store.example_lookup_exact(kind, value)
        if not rows:
            return {"status": "not_found", "message": NOT_FOUND, "lookup": {"kind": kind, "value": value}}
        if kind == "km_id" and has_detail and len(rows) > 1:
            docs = prepare_from_frame(rows, text_fields=("nl", "nl_variants", "script_description", "file_name"))
            ranked = HybridIndex(docs).rank_ids(prompt, [row.get("id") for row in rows])
            order = {hit.id: pos for pos, hit in enumerate(ranked)}
            rows.sort(key=lambda row: order.get(str(row.get("id")), len(order)))
        usable = [row for row in rows if row.get("sql") is not None]
        if not usable:
            return {"status": "not_found", "message": NOT_FOUND, "lookup": {"kind": kind, "value": value}}
        parts: list[str] = []
        for pos, row in enumerate(usable, 1):
            if len(usable) > 1:
                parts.append(f"### Вариант {pos}")
            parts.extend([
                f"SCRIPT_ID: {row.get('script_id')}",
                f"KM_ID: {row.get('km_id') or 'не указан'}",
                markdown_fence(row["sql"] if isinstance(row["sql"], str) else str(row["sql"])),
                f"**Описание:**\n{row.get('script_description') or 'Описание отсутствует.'}",
            ])
        return {"status": "found", "lookup": {"kind": kind, "value": value}, "count": len(usable), "script_ids": [row.get("script_id") for row in usable], "verbatim": True, "content": "\n\n".join(parts)}

    def search(self, query: str, *, corpus: str, top_k: int = 5, filters: Mapping[str, Any] | None = None, mode: str = "search", ids: Iterable[Any] | None = None) -> dict[str, Any]:
        if corpus not in {"tables", "columns", "examples"}:
            raise ValueError("corpus must be tables, columns, or examples")
        if not self.index_root:
            raise KbStoreError("Published hybrid index root is not configured", code="index_not_ready")
        resolved_filters = self._resolve_search_filters(corpus, filters or {})
        try:
            index, manifest = load_index(
                self.index_root,
                corpus,
                embedder=self.embedder if corpus != "columns" else None,
                reranker=self.reranker if corpus != "columns" else None,
                model_key=self.model_key,
            )
        except (OSError, ValueError, json.JSONDecodeError, ImportError) as exc:
            raise KbStoreError(f"Published {corpus} index is unavailable: {exc}", code="index_not_ready") from exc

        current_signature = self.store.current_source_signature(corpus)
        index_stale = manifest.get("source_signature") != current_signature
        if index_stale:
            logger.warning("SQL Assistant %s index is stale: build=%s", corpus, manifest.get("build_id"))

        allowed_by_filters = _indexed_ids_matching(corpus, index, resolved_filters)
        if mode == "rank_ids":
            supplied_ids = _normalized_ids(ids or [])
            allowed_set = set(allowed_by_filters) if allowed_by_filters is not None else None
            restricted_ids = [doc_id for doc_id in supplied_ids if allowed_set is None or doc_id in allowed_set]
        else:
            restricted_ids = allowed_by_filters
        if mode == "rank_ids":
            outcome = index.rank_ids_with_diagnostics(query, restricted_ids, top_k=top_k, score_floor=self.score_floor)
            hits = outcome.hits
            candidates_total, dropped_below = outcome.candidates_total, outcome.dropped_below_floor
            dropped_groups, low_confidence = outcome.dropped_by_group_collapse, outcome.low_confidence
        else:
            outcome = index.search_with_diagnostics(
                query,
                top_k=top_k,
                ids=restricted_ids if restricted_ids is not None else None,
                score_floor=self.score_floor,
                allow_low_confidence_fallback=True,
            )
            hits = outcome.hits
            candidates_total = outcome.candidates_total
            dropped_below = outcome.dropped_below_floor
            dropped_groups = outcome.dropped_by_group_collapse
            low_confidence = outcome.low_confidence

        live_rows = self.store.corpus_by_ids(corpus, [hit.id for hit in hits])
        live_by_id = {str(row.get("id")): row for row in live_rows}
        items = []
        for hit in hits:
            row = live_by_id.get(hit.id)
            if row is None or not _row_matches(corpus, row, resolved_filters):
                continue
            item = _compact_item(corpus, row)
            item.update({"score": hit.score, "dense_score": hit.dense_score, "bm25_score": hit.bm25_score, "fused_score": hit.fused_score})
            items.append(item)
        dropped_missing = len(hits) - len(items)
        return {
            "status": "ok",
            "corpus": corpus,
            "build_id": manifest["build_id"],
            "source_signature": current_signature,
            "index_stale": index_stale,
            "items": items,
            "scores": [item["score"] for item in items],
            "low_confidence": low_confidence or not items,
            "candidates_total": candidates_total,
            "dropped_below_floor": dropped_below,
            "dropped_by_group_collapse": dropped_groups,
            "dropped_missing_from_kb": dropped_missing,
        }

    def _resolve_search_filters(self, corpus: str, filters: Mapping[str, Any]) -> dict[str, Any]:
        supported = {"dialect", "group_keys", "table_ids"}
        unknown = sorted(key for key, value in filters.items() if key not in supported and value not in (None, "", []))
        if unknown:
            raise KbStoreError(f"Unsupported {corpus} filters: {unknown}", code="unsupported_filter")
        resolved: dict[str, Any] = {}
        if filters.get("dialect"):
            if corpus == "columns":
                raise KbStoreError("columns does not contain dialect; filters.dialect is unsupported", code="unsupported_filter")
            resolved["dialect"] = str(filters["dialect"]).lower()
        group_keys = _filter_values(filters.get("group_keys"))
        table_ids = _filter_values(filters.get("table_ids"))
        if corpus == "columns" and group_keys:
            raise KbStoreError("columns filters.group_keys requires a table join and is unsupported", code="unsupported_filter")
        if corpus == "examples" and (group_keys or table_ids):
            tables = []
            if group_keys:
                tables.extend(self.store.tables_by_group_keys(group_keys))
            if table_ids:
                known = {str(row.get("id")) for row in tables}
                tables.extend(row for row in self.store.tables_by_ids(table_ids) if str(row.get("id")) not in known)
            resolved["example_table_names"] = {
                str(table.get("table_name") or "").strip().lower()
                for table in tables
                if str(table.get("table_name") or "").strip()
            }
        else:
            if group_keys:
                resolved["group_keys"] = set(group_keys)
            if table_ids:
                resolved["table_ids"] = set(table_ids)
        return resolved

    async def describe(self, *, table_ids: Iterable[Any] = (), group_keys: Iterable[str] = (), example_ids: Iterable[Any] = (), detail: str = "summary", column_query: str = "", max_columns: int = 30, live_schema: bool = False, live_timeout_sec: float = 30.0) -> dict[str, Any]:
        tables = self.store.tables_by_ids(table_ids)
        if group_keys:
            known = {str(row.get("id")) for row in tables}
            tables.extend(row for row in self.store.tables_by_group_keys(group_keys) if str(row.get("id")) not in known)
        columns = self.store.columns_for_tables([row.get("id") for row in tables]) if tables and detail == "full" else []
        cards = []
        for table in tables:
            selected, omitted = _select_columns([c for c in columns if str(c.get("table_id")) == str(table.get("id"))], column_query, max_columns)
            live = {"status": "not_requested"}
            if live_schema:
                live = await SparkBackend.list_columns(str(table.get("table_name")), timeout_sec=live_timeout_sec)
            cards.append({**table, "selected_columns": selected, "omitted_columns": omitted, "live_schema_status": live.get("status"), "live_schema": live.get("columns", [])})
        examples = self.store.examples_by_ids(example_ids)
        return {"status": "ok", "tables": cards, "examples": examples}

    async def validate(self, sql: str, *, dialect: str, table_ids: Iterable[Any] = (), live_analyze: bool = False, generated: bool = True, timeout_sec: float = 60.0) -> dict[str, Any]:
        result = await self._validate_for_repair(sql, dialect=dialect, table_ids=table_ids, live_analyze=live_analyze, generated=generated, timeout_sec=timeout_sec)
        return _delivery_gate(result)

    async def _validate_for_repair(self, sql: str, *, dialect: str, table_ids: Iterable[Any] = (), live_analyze: bool = False, generated: bool = True, timeout_sec: float = 60.0) -> dict[str, Any]:
        ids = list(table_ids)
        schema = self.store.schema_for_tables(ids) if ids else {}
        result = validate_sql(sql, dialect=dialect, schema=schema, generated=generated)
        result["spark"] = {"status": "not_requested"}
        if result["valid"] and live_analyze and dialect == "spark":
            result["spark"] = await SparkBackend.analyze(result["sql"], timeout_sec=timeout_sec)
            if result["spark"].get("status") == "invalid":
                result["valid"] = False
                result["status"] = "invalid"
                result["issues"].append({"code": "spark_analysis", "message": result["spark"].get("error")})
            elif result["spark"].get("status") != "valid":
                result["valid"] = False
                result["status"] = result["spark"].get("status", "unavailable")
                result["issues"].append({"code": "spark_analysis_unavailable", "message": "Requested live analysis did not complete"})
        result["validation_scope"] = "spark_analysis" if result["spark"].get("status") == "valid" else "static_only"
        return result

    def facts(self, sql: str, *, dialect: str, table_ids: Iterable[Any] = (), example_ids: Iterable[Any] = ()) -> dict[str, Any]:
        ids = list(table_ids)
        schema = self.store.schema_for_tables(ids) if ids else {}
        tables = self.store.tables_by_ids(ids) if ids else []
        descriptions = {row.get("table_name"): row.get("description") for row in tables}
        result = sql_facts(sql, dialect=dialect, schema=schema, descriptions=descriptions)
        examples = self.store.examples_by_ids(example_ids)
        result["example_notes"] = [{"id": row.get("id"), "script_id": row.get("script_id"), "description": row.get("script_description")} for row in examples]
        return result

    async def generate(self, *, question: str, dialect: str, table_ids: Iterable[Any], example_ids: Iterable[Any] = (), column_query: str = "", prior_sql: str = "", feedback: str = "", max_repairs: int = 2, live_analyze: bool = False, llm_config: Mapping[str, Any] | None = None, llm_timeout_sec: float = 180.0) -> dict[str, Any]:
        requested_table_ids = _normalized_ids(table_ids)
        if not requested_table_ids:
            return {"status": "grounding_error", "error_type": "missing_table_ids", "requested_table_ids": [], "missing_table_ids": [], "error": "Grounded SQL generation requires at least one table_id"}
        resolved_tables = self.store.tables_by_ids(requested_table_ids)
        resolved_table_ids = {str(row.get("id")) for row in resolved_tables}
        missing_table_ids = [value for value in requested_table_ids if value not in resolved_table_ids]
        if missing_table_ids or not resolved_tables:
            return {"status": "grounding_error", "error_type": "unknown_table_id", "requested_table_ids": requested_table_ids, "missing_table_ids": missing_table_ids or requested_table_ids, "error": "One or more requested table_ids are absent from the SQL Assistant KB"}

        requested_example_ids = _normalized_ids(example_ids)
        resolved_examples = self.store.examples_by_ids(requested_example_ids) if requested_example_ids else []
        resolved_example_ids = {str(row.get("id")) for row in resolved_examples}
        missing_example_ids = [value for value in requested_example_ids if value not in resolved_example_ids]
        cards = await self.describe(table_ids=requested_table_ids, example_ids=sorted(resolved_example_ids), detail="full", column_query=column_query)
        rules = spark_rules() if dialect == "spark" else greenplum_rules()
        payload = {"question": question, "dialect": dialect, "tables": cards["tables"], "examples": cards["examples"], "prior_sql": prior_sql, "feedback": feedback}
        prompt = rules + "\nReturn only SQL, preferably in a sql markdown fence.\nGrounding:\n" + json.dumps(payload, ensure_ascii=False, default=str)
        from lib.services.llm_client import call_llm
        cfg = dict(llm_config) if llm_config is not None else _default_llm_config()
        response = await asyncio.wait_for(asyncio.to_thread(call_llm, [{"role": "system", "content": "You generate grounded read-only SQL."}, {"role": "user", "content": ("/no_think\n" if _is_qwen(cfg) else "") + prompt}], cfg=cfg, timeout=llm_timeout_sec), timeout=llm_timeout_sec + 1)
        validation = await self._validate_for_repair(response, dialect=dialect, table_ids=requested_table_ids, live_analyze=live_analyze)
        attempts = []
        seen = {re.sub(r"\s+", " ", validation["sql"]).strip().lower()}
        seen_issues: set[tuple[str, ...]] = set()
        for _ in range(max(0, min(2, int(max_repairs)))):
            if validation["valid"]:
                break
            if validation["status"] != "invalid":
                break
            issue_signature = tuple(sorted(str(issue.get("code")) for issue in validation.get("issues", [])))
            if issue_signature in seen_issues:
                validation["warnings"].append({"code": "repeated_issue", "message": "The same validation issue repeated; stopped early"})
                break
            seen_issues.add(issue_signature)
            attempts.append({"issues": validation["issues"]})
            fix_prompt = rules + "\nFix the SQL using only supplied grounding. Return only SQL.\n" + json.dumps({"sql": validation["sql"], "issues": validation["issues"], "grounding": payload}, ensure_ascii=False, default=str)
            response = await asyncio.wait_for(asyncio.to_thread(call_llm, [{"role": "system", "content": "Repair generated SQL without inventing schema."}, {"role": "user", "content": fix_prompt}], cfg=cfg, timeout=llm_timeout_sec), timeout=llm_timeout_sec + 1)
            validation = await self._validate_for_repair(response, dialect=dialect, table_ids=requested_table_ids, live_analyze=live_analyze)
            normalized = re.sub(r"\s+", " ", validation["sql"]).strip().lower()
            if normalized in seen:
                validation["warnings"].append({"code": "repeated_issue", "message": "Repair returned equivalent SQL; stopped early"})
                break
            seen.add(normalized)
        facts: dict[str, Any] = {}
        if validation["valid"] and validation.get("sql"):
            try:
                facts = self.facts(validation["sql"], dialect=dialect, table_ids=requested_table_ids, example_ids=sorted(resolved_example_ids))
            except Exception as exc:
                facts = {"status": "invalid", "error": str(exc), "error_type": type(exc).__name__}
        public_validation = _delivery_gate(validation)
        return {"status": "ok" if validation["valid"] else public_validation["status"], "valid": validation["valid"], "publishable": public_validation["publishable"], "sql": public_validation["sql"], "delivery": public_validation["delivery"], "validation": public_validation, "repair_attempts": attempts, "facts": facts, "missing_example_ids": missing_example_ids, "grounding_warnings": ([{"code": "unknown_example_id", "ids": missing_example_ids}] if missing_example_ids else [])}


def _delivery_gate(validation: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(validation)
    publishable = result.get("valid") is True and result.get("status") == "valid"
    result["publishable"] = publishable
    result["delivery"] = {
        "action": "deliver_validated_sql" if publishable else "explain_validation_failure",
        "instruction": (
            "SQL прошёл указанную проверку. Не утверждай выполнение на реальных данных."
            if publishable else
            "Не выдавай SQL-блок и не называй запрос корректным. Сообщи, что проверка не пройдена, перечисли issues и запроси уточнение метаданных либо исправление."
        ),
    }
    if not publishable:
        result["sql"] = ""
        spark = dict(result.get("spark") or {})
        spark.pop("sql", None)
        result["spark"] = spark
        result.pop("ast", None)
    return result


def _compact_item(corpus: str, row: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {"tables": ("id", "table_name", "group_key", "layer", "description", "row_count", "dialect"), "columns": ("id", "table_id", "table_name", "column_name", "data_type", "description"), "examples": ("id", "script_id", "km_id", "file_name", "file_path", "nl", "script_description", "tables", "dialect")}[corpus]
    item = {key: row.get(key) for key in allowed}
    if corpus == "examples" and row.get("sql"):
        item["sql_preview"] = str(row["sql"])[:240]
    return item


def _normalized_ids(values: Iterable[Any]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if value is not None and str(value) != ""))


def _filter_values(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, (str, bytes)):
        return [str(value)]
    return _normalized_ids(value)


def _string_values(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, (list, tuple, set)):
        return {str(item) for item in value}
    text = str(value).strip()
    if not text:
        return set()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return {str(item) for item in parsed}
    except (TypeError, ValueError):
        pass
    return {part.strip() for part in text.split(",") if part.strip()}


def _row_matches(corpus: str, row: Mapping[str, Any], filters: Mapping[str, Any]) -> bool:
    if filters.get("dialect") and str(row.get("dialect", "")).lower() != filters["dialect"]:
        return False
    if corpus == "tables":
        if filters.get("group_keys") and str(row.get("group_key")) not in filters["group_keys"]:
            return False
        if filters.get("table_ids") and str(row.get("id")) not in filters["table_ids"]:
            return False
    elif corpus == "columns":
        if filters.get("table_ids") and str(row.get("table_id")) not in filters["table_ids"]:
            return False
    else:
        memberships = {value.lower() for value in _string_values(row.get("tables"))}
        if "example_table_names" in filters and memberships.isdisjoint(filters["example_table_names"]):
            return False
    return True


def _indexed_ids_matching(corpus: str, index: HybridIndex, filters: Mapping[str, Any]) -> list[str] | None:
    """Use compact index metadata to restrict retrieval; live rows are checked again."""
    if not filters:
        return None
    return [document.id for document in index.documents if _row_matches(corpus, document.metadata, filters)]


def _select_columns(columns: list[dict[str, Any]], query: str, limit: int) -> tuple[list[dict[str, Any]], int]:
    terms = set(re.findall(r"[\w]+", query.lower()))
    def priority(col: Mapping[str, Any]) -> tuple[int, int, str]:
        name = str(col.get("column_name") or "")
        hay = (name + " " + str(col.get("description") or "")).lower()
        relevant = bool(terms.intersection(re.findall(r"[\w]+", hay)))
        keyish = bool(re.search(r"(?:date|time|dt|_id|_key|_num)$", name, re.I))
        return (0 if relevant else 1 if keyish else 2, int(col.get("ordinal") or 10**9), name)
    ordered = sorted(columns, key=priority)
    selected = ordered[: max(0, int(limit))]
    return selected, max(0, len(ordered) - len(selected))


def _is_qwen(config: Mapping[str, Any] | None) -> bool:
    return "qwen" in str((config or {}).get("model", "")).lower()


def _default_llm_config() -> dict[str, Any]:
    from config import SETTINGS
    from lib.services.llm_config import resolve_llm_config
    policy = ((SETTINGS.get("skills") or {}).get("sql_assistant") or {}).get("llm") or {}
    return resolve_llm_config({"llm_max_tokens": policy.get("max_tokens", 8192), "llm_temperature": policy.get("temperature", 0.1)})


def structured_error(exc: Exception) -> dict[str, Any]:
    return {"status": getattr(exc, "code", "error"), "error": str(exc), "error_type": type(exc).__name__}
