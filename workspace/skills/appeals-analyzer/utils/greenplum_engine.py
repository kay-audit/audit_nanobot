"""Greenplum structural population filter and full-row hydration for appeals."""
from __future__ import annotations

import csv
import io
import json
import re
import logging
import time
from datetime import date, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, Sequence
import pandas as pd

try:
    from . import db
except ImportError:
    import db

logger = logging.getLogger(__name__)


TABLE_PREFIX = "40_kaluginvs_anofl_"
DEFAULT_YEARS = [2023, 2024, 2025, 2026]
DEFAULT_SCHEMA = "s_grnplm_ld_audit_da_project_27"

# Единственный источник канонических значений для skill и WEB-интерфейса.
_CANONICAL_FILTERS_PATH = Path(__file__).resolve().parents[1] / "canonical_filters.json"
with _CANONICAL_FILTERS_PATH.open("r", encoding="utf-8") as _catalog_file:
    _CANONICAL_FILTERS = json.load(_catalog_file)

PRD_CANONICAL = _CANONICAL_FILTERS["prd"]
S_PRD_CANONICAL = _CANONICAL_FILTERS["s_prd"]
CHNL_CANONICAL = _CANONICAL_FILTERS["chnl"]

_CANONICAL_TITLE = "Анализ обращений"
_CANONICAL_GROUPS = {
    "Продукт:": ("products", PRD_CANONICAL, "prd"),
    "Подпродукт:": ("subproducts", S_PRD_CANONICAL, "s_prd"),
    "Канал:": ("channels", CHNL_CANONICAL, "chnl"),
}


def normalize_id(value: Any) -> str:
    return str(value).strip()


def _canonicalize_field(value: str, canonical: Sequence[str], label: str) -> List[str]:
    value = (value or "").strip()
    if not value:
        return []
    lookup = {item.strip().casefold(): item for item in canonical}
    whole = lookup.get(value.casefold())
    if whole is not None:
        return [whole]
    tokens = tuple(part.strip().casefold() for part in value.split(","))
    if any(not token for token in tokens):
        raise ValueError(f"Невозможно разобрать список {label}: пустое значение между запятыми.")
    candidates = sorted(
        {(tuple(part.strip().casefold() for part in item.split(",")), item) for item in canonical},
        key=lambda pair: (-len(pair[0]), pair[1].casefold()),
    )
    memo: Dict[int, List[Tuple[str, ...]]] = {}

    def segment(position: int) -> List[Tuple[str, ...]]:
        if position == len(tokens):
            return [tuple()]
        if position in memo:
            return memo[position]
        variants: List[Tuple[str, ...]] = []
        for parts, canonical_value in candidates:
            end = position + len(parts)
            if tokens[position:end] != parts:
                continue
            for tail in segment(end):
                variant = (canonical_value,) + tail
                if variant not in variants:
                    variants.append(variant)
                if len(variants) > 1:
                    memo[position] = variants
                    return variants
        memo[position] = variants
        return variants

    variants = segment(0)
    if not variants:
        raise ValueError(f"Невозможно разобрать список {label} '{value}' по каноническому справочнику.")
    if len(variants) > 1:
        raise ValueError(f"Неоднозначный список {label} '{value}'; уточните канонические значения.")
    return list(dict.fromkeys(variants[0]))


def _canonicalize_json_values(value: Any, canonical: Sequence[str], label: str) -> List[str]:
    if not isinstance(value, list):
        raise ValueError(f"Фильтр {label} должен быть JSON-массивом.")
    if any(not isinstance(item, str) for item in value):
        raise ValueError(f"Все значения фильтра {label} должны быть строками.")
    allowed = set(canonical)
    unknown = [item for item in value if item not in allowed]
    if unknown:
        raise ValueError(
            f"Фильтр {label} содержит неизвестные значения: {', '.join(unknown)}."
        )
    return list(dict.fromkeys(value))


def _parse_optional_date(value: Any, label: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Поле {label} должно быть датой YYYY-MM-DD или null.")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError(f"Поле {label} должно быть корректной датой YYYY-MM-DD.") from exc


def _parse_display_date(value: str) -> str:
    match = re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{4})", value.strip())
    if not match:
        raise ValueError("Дата в canonical Appeals message должна иметь формат DD.MM.YYYY.")
    day, month, year = (int(part) for part in match.groups())
    try:
        return date(year, month, day).isoformat()
    except ValueError as exc:
        raise ValueError("Canonical Appeals message содержит некорректную дату.") from exc


def _parse_canonical_period(value: str) -> Tuple[Optional[str], Optional[str]]:
    value = value.strip()
    if value.startswith("с "):
        return _parse_display_date(value[2:]), None
    if value.startswith("по "):
        return None, _parse_display_date(value[3:])
    parts = value.split(" — ")
    if len(parts) != 2:
        raise ValueError("Неверный период в canonical Appeals message.")
    date_from = _parse_display_date(parts[0])
    date_to = _parse_display_date(parts[1])
    if date_from > date_to:
        raise ValueError("Дата начала периода не может быть позже даты окончания.")
    return date_from, date_to


def _parse_canonical_analytical_request(message: str) -> Optional[Dict[str, Any]]:
    """Parse the human-readable envelope emitted by Audit Workstation."""
    normalized = (message or "").replace("\r\n", "\n").replace("\r", "\n")
    request_marker = re.search(r"(?m)^Запрос:\s*$", normalized)
    if request_marker is None:
        return None

    prefix = normalized[:request_marker.start()].strip()
    blocks = re.split(r"\n\s*\n", prefix) if prefix else []
    if not blocks or blocks[0].strip() != _CANONICAL_TITLE:
        return None

    parsed_groups: Dict[str, List[str]] = {
        "products": [], "subproducts": [], "channels": [],
    }
    date_range: Optional[Tuple[Optional[str], Optional[str]]] = None
    seen_headers = set()
    for block in blocks[1:]:
        lines = block.splitlines()
        header = lines[0].strip() if lines else ""
        if header in seen_headers:
            raise ValueError(f"Canonical Appeals message повторяет секцию {header}")
        seen_headers.add(header)

        group = _CANONICAL_GROUPS.get(header)
        if group is not None:
            if len(lines) < 2 or any(not line.startswith("- ") for line in lines[1:]):
                raise ValueError(f"Значения секции {header} должны быть bullet-строками.")
            values = [line[2:] for line in lines[1:]]
            target, canonical, label = group
            parsed_groups[target] = _canonicalize_json_values(values, canonical, label)
            continue

        if header == "Период:":
            if len(lines) != 2:
                raise ValueError("Секция Период должна содержать ровно одну строку.")
            date_range = _parse_canonical_period(lines[1])
            continue

        raise ValueError(f"Неизвестная секция canonical Appeals message: {header or '<пусто>'}.")

    query = normalized[request_marker.end():].strip()
    if not query:
        raise ValueError("Секция Запрос canonical Appeals message не может быть пустой.")
    return {
        **parsed_groups,
        "query": query,
        "date_range": date_range,
        "format": "canonical",
    }


def _parse_json_analytical_request(message: str) -> Optional[Dict[str, Any]]:
    try:
        payload = json.loads(message)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict) or payload.get("request_type") != "appeals_analysis":
        return None

    filters = payload.get("filters")
    if not isinstance(filters, dict):
        raise ValueError("Поле filters должно быть JSON-объектом.")
    required_filters = {"prd", "s_prd", "chnl", "date_from", "date_to"}
    missing = required_filters - set(filters)
    if missing:
        raise ValueError(f"В filters отсутствуют поля: {', '.join(sorted(missing))}.")

    products = _canonicalize_json_values(filters["prd"], PRD_CANONICAL, "prd")
    subproducts = _canonicalize_json_values(filters["s_prd"], S_PRD_CANONICAL, "s_prd")
    channels = _canonicalize_json_values(filters["chnl"], CHNL_CANONICAL, "chnl")
    date_from = _parse_optional_date(filters["date_from"], "date_from")
    date_to = _parse_optional_date(filters["date_to"], "date_to")
    if date_from and date_to and date_from > date_to:
        raise ValueError("Дата начала периода не может быть позже даты окончания.")

    query = payload.get("prompt")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("Поле prompt не может быть пустым.")
    date_range = (date_from, date_to) if date_from or date_to else None
    return {
        "products": products,
        "subproducts": subproducts,
        "channels": channels,
        "query": query.strip(),
        "date_range": date_range,
        "format": "json",
    }


def parse_structured_analytical_request(message: str) -> Dict[str, Any]:
    """Parse canonical text, appeals_analysis JSON, then legacy quoted CSV."""
    canonical_request = _parse_canonical_analytical_request(message)
    if canonical_request is not None:
        return canonical_request

    json_request = _parse_json_analytical_request(message)
    if json_request is not None:
        return json_request

    quoted = r'"(?:[^"]|"")*"'
    if not re.fullmatch(rf"\s*{quoted}\s*,\s*{quoted}\s*,\s*{quoted}\s*,\s*{quoted}\s*", message or ""):
        raise ValueError(
            "Ожидается canonical Appeals message, appeals_analysis JSON "
            "или ровно четыре quoted CSV-поля."
        )
    try:
        rows = list(csv.reader(io.StringIO(message or ""), skipinitialspace=True))
    except csv.Error as exc:
        raise ValueError("Неверный CSV-формат аналитического запроса.") from exc
    if len(rows) != 1 or len(rows[0]) != 4:
        raise ValueError("Аналитический запрос должен состоять ровно из четырёх CSV-полей.")
    products = _canonicalize_field(rows[0][0], PRD_CANONICAL, "продукта")
    subproducts = _canonicalize_field(rows[0][1], S_PRD_CANONICAL, "субпродукта")
    channels = _canonicalize_field(rows[0][2], CHNL_CANONICAL, "канала")
    query = rows[0][3]
    if not query or not query.strip():
        raise ValueError("Четвёртое поле — смысловой запрос — не может быть пустым.")
    return {
        "products": products,
        "subproducts": subproducts,
        "channels": channels,
        "query": query,
        "date_range": None,
        "format": "legacy",
    }


def is_structured_analytical_request(message: str) -> bool:
    try:
        parse_structured_analytical_request(message)
        return True
    except ValueError:
        return False


def _quote_literals(values: Sequence[str]) -> str:
    return ", ".join("'" + str(value).replace("'", "''") + "'" for value in values)


def _appeal_table(year: int) -> str:
    return f'"{DEFAULT_SCHEMA}"."{TABLE_PREFIX}appeal_{year}"'


def _dialog_table(year: int) -> str:
    return f'"{DEFAULT_SCHEMA}"."{TABLE_PREFIX}appeal_dialogs_{year}"'


def _task_table(year: int) -> str:
    return f'"{DEFAULT_SCHEMA}"."{TABLE_PREFIX}appeal_task_{year}"'


def select_gp_years(
    date_range: Optional[Tuple[Optional[str], Optional[str]]],
    configured_years: Sequence[int] = DEFAULT_YEARS,
) -> List[int]:
    """Prune yearly GP tables to the validated inclusive request range."""
    available = list(dict.fromkeys(int(year) for year in configured_years))
    if date_range is None:
        return available
    start_raw, end_raw = date_range
    start_date = date.fromisoformat(start_raw) if start_raw else None
    end_date = date.fromisoformat(end_raw) if end_raw else None
    if start_date and end_date and start_date > end_date:
        raise ValueError("date_range start must not be after end")
    return [
        year for year in available
        if (start_date is None or year >= start_date.year)
        and (end_date is None or year <= end_date.year)
    ]


def _date_bounds(
    date_range: Optional[Tuple[Optional[str], Optional[str]]],
) -> Optional[Tuple[Optional[date], Optional[date]]]:
    if date_range is None:
        return None
    start_raw, end_raw = date_range
    start_date = date.fromisoformat(start_raw) if start_raw else None
    end_date = date.fromisoformat(end_raw) if end_raw else None
    if start_date and end_date and start_date > end_date:
        raise ValueError("date_range start must not be after end")
    return start_date, (end_date + timedelta(days=1)) if end_date else None


def build_product_prefilter_sql(
    products: Sequence[str],
    subproducts: Sequence[str],
    channels: Sequence[str] = (),
    years: Sequence[int] = DEFAULT_YEARS,
    date_range: Optional[Tuple[Optional[str], Optional[str]]] = None,
) -> Tuple[str, List[Any]]:
    """Return an ID-only structural query with optional canonical req_reg_date pushdown."""
    if not products and not subproducts and not channels:
        raise ValueError("SQL structural prefilter requires product, subproduct or channel filters.")
    selected_years = select_gp_years(date_range, years)
    if not selected_years:
        raise ValueError("date_range does not intersect configured Greenplum years")
    structural_conditions = []
    condition_params: List[Any] = []
    if products:
        structural_conditions.append(f"a.prd IN ({', '.join(['%s'] * len(products))})")
        condition_params.extend(products)
    if subproducts:
        structural_conditions.append(f"a.s_prd IN ({', '.join(['%s'] * len(subproducts))})")
        condition_params.extend(subproducts)
    if channels:
        structural_conditions.append(f"a.chnl IN ({', '.join(['%s'] * len(channels))})")
        condition_params.extend(channels)

    # Независимые группы prd/s_prd/chnl образуют единую OR-группу. Период
    # добавляется ниже как отдельное AND-ограничение ко всему результату.
    condition_templates = [f"({' OR '.join(structural_conditions)})"]
    bounds = _date_bounds(date_range)
    if bounds is not None:
        start_date, exclusive_end = bounds
        if start_date is not None:
            condition_templates.append("a.req_reg_date >= %s")
            condition_params.append(start_date)
        if exclusive_end is not None:
            condition_templates.append("a.req_reg_date < %s")
            condition_params.append(exclusive_end)
    where = " AND ".join(condition_templates)
    sql = "\nUNION ALL\n".join(
        f"SELECT COALESCE(CAST(a.id AS VARCHAR), CAST(a.app_row_id AS VARCHAR), "
        f"CAST(a.req_row_id AS VARCHAR)) AS id FROM {_appeal_table(year)} a WHERE {where}"
        for year in selected_years
    )
    return sql, condition_params * len(selected_years)


def _run_sql_on_connection(
    conn: Any,
    sql: str,
    params: Optional[Sequence[Any]] = None,
    *,
    label: str = "query",
) -> Optional[pd.DataFrame]:
    """Execute one SQL statement on an already leased shared connection."""
    started = time.monotonic()
    query_params = tuple(params) if params else None
    with conn.cursor() as cursor:
        if query_params is not None:
            cursor.execute(sql, query_params)
        else:
            cursor.execute(sql)
        if cursor.description is None:
            return None
        columns = [description[0] for description in cursor.description]
        rows = list(cursor.fetchall())
    frame = pd.DataFrame(rows, columns=columns) if columns and rows else None
    logger.info(
        "Greenplum %s finished: rows=%s elapsed=%.2fs",
        label,
        0 if frame is None else len(frame),
        time.monotonic() - started,
    )
    return frame


def _run_sql(sql: str, params: Optional[Sequence[Any]] = None) -> Optional[pd.DataFrame]:
    logger.debug("[appeals] executing GP query via shared db pool")
    return db.run(
        lambda conn: _run_sql_on_connection(conn, sql, params, label="query")
    )


def fetch_candidate_ids_by_product(
    products: Sequence[str],
    subproducts: Sequence[str],
    channels: Sequence[str] = (),
    date_range: Optional[Tuple[Optional[str], Optional[str]]] = None,
) -> List[str]:
    if not products and not subproducts and not channels:
        raise ValueError("Structural prefilter must be skipped when product, subproduct and channel are empty.")
    selected_years = select_gp_years(date_range)
    logger.info("GP structural prefilter date_range=%s selected_years=%s", date_range, selected_years)
    if not selected_years:
        logger.info("GP structural population count after product/subproduct/channel/date=0")
        return []
    sql, params = build_product_prefilter_sql(
        products, subproducts, channels, years=selected_years, date_range=date_range,
    )
    frame = _run_sql(sql, params)
    if frame is None or frame.empty:
        logger.info("GP structural population count after product/subproduct/channel/date=0")
        return []
    ids = list(dict.fromkeys(normalize_id(value) for value in frame["id"] if normalize_id(value)))
    logger.info("GP structural population count after product/subproduct/channel/date=%s", len(ids))
    return ids


def build_hydration_sql(candidate_ids: Sequence[str], years: Sequence[int] = DEFAULT_YEARS) -> str:
    """Fetch base appeal rows only; related rows are hydrated separately."""
    normalized = list(dict.fromkeys(normalize_id(value) for value in candidate_ids if normalize_id(value)))
    if not normalized:
        raise ValueError("Hydration requires at least one candidate ID.")
    ids = _quote_literals(normalized)
    select_clause = """SELECT {year} AS source_year,
        COALESCE(CAST(a.id AS VARCHAR), CAST(a.app_row_id AS VARCHAR), CAST(a.req_row_id AS VARCHAR)) AS id,
        CAST(a.app_row_id AS VARCHAR) AS app_row_id, CAST(a.req_row_id AS VARCHAR) AS req_row_id,
        CAST(a.cust_epk_id AS VARCHAR) AS cust_epk_id,
        CAST(a.req_reg_date AS VARCHAR) AS req_reg_date, CAST(a.req_reg_date AS VARCHAR) AS date,
        CAST(a.created AS VARCHAR) AS created, CAST(a.req_created AS VARCHAR) AS req_created,
        CAST(a.app_created AS VARCHAR) AS app_created,
        a.grp, a.prd, a.s_prd, a.chnl, a.kanal_reg, a.subj, a.s_subj,
        CAST(a.toxic_flag AS VARCHAR) AS toxic_flag, CAST(a.toxic_flag_rep AS VARCHAR) AS toxic_flag_rep,
        a.req_cons_res_val, a.req_status, a.app_status, a.req_desc AS short_description, a.app_content"""
    return "\nUNION ALL\n".join(
        f"{select_clause.format(year=year)} FROM {_appeal_table(year)} a "
        f"WHERE COALESCE(CAST(a.id AS VARCHAR), CAST(a.app_row_id AS VARCHAR), "
        f"CAST(a.req_row_id AS VARCHAR)) IN ({ids})"
        for year in years
    )


def _related_ids_by_year(base: pd.DataFrame) -> Dict[int, List[str]]:
    result: Dict[int, List[str]] = {}
    for _, row in base.iterrows():
        app_row_id = normalize_id(row.get("app_row_id"))
        if not app_row_id:
            continue
        year = int(row.get("source_year"))
        result.setdefault(year, [])
        if app_row_id not in result[year]:
            result[year].append(app_row_id)
    return result


def build_dialog_hydration_sql(ids_by_year: Dict[int, Sequence[str]]) -> str:
    selects = []
    for year in sorted(ids_by_year):
        ids = _quote_literals(ids_by_year[year])
        selects.append(
            f"SELECT {year} AS source_year, CAST(d.app_row_id AS VARCHAR) AS app_row_id, "
            f"d.msg_crm_chat, d.msg_pprb_chat, d.msg_sc_chat FROM {_dialog_table(year)} d "
            f"WHERE CAST(d.app_row_id AS VARCHAR) IN ({ids})"
        )
    if not selects:
        raise ValueError("Dialog hydration requires app_row_ids.")
    return "\nUNION ALL\n".join(selects)


def build_task_hydration_sql(ids_by_year: Dict[int, Sequence[str]]) -> str:
    selects = []
    for year in sorted(ids_by_year):
        ids = _quote_literals(ids_by_year[year])
        selects.append(
            f"SELECT {year} AS source_year, CAST(t.app_row_id AS VARCHAR) AS app_row_id, "
            "t.task_id, t.task_type AS task_name, t.task_status, "
            "COALESCE(t.task_text_sol, t.task_answer_full, t.task_answer) AS task_desc, "
            "t.task_result, COALESCE(t.task_executor_division, t.task_div_name, t.task_executor) AS exec_dept "
            f"FROM {_task_table(year)} t WHERE CAST(t.app_row_id AS VARCHAR) IN ({ids})"
        )
    if not selects:
        raise ValueError("Task hydration requires app_row_ids.")
    return "\nUNION ALL\n".join(selects)


def _nonempty_unique(series: pd.Series) -> List[str]:
    values: List[str] = []
    for value in series:
        if pd.notna(value):
            cleaned = str(value).strip()
            if cleaned and cleaned not in values:
                values.append(cleaned)
    return values


def _first_nonempty(series: pd.Series) -> Any:
    values = _nonempty_unique(series)
    return values[0] if values else None


def _join_fragments(series: pd.Series) -> Optional[str]:
    values = _nonempty_unique(series)
    return "\n\n".join(values) if values else None


def _standardize_df_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    result = df.copy()
    if "short_description" in result and "Короткое описание" not in result:
        result["Короткое описание"] = result["short_description"]
    if "msg_pprb_chat" in result and "Транскрибация диалога" not in result:
        result["Транскрибация диалога"] = result["msg_pprb_chat"]
    elif "description" in result and "Транскрибация диалога" not in result:
        result["Транскрибация диалога"] = result["description"]
    return result


def normalize_hydrated_appeals(df: pd.DataFrame) -> pd.DataFrame:
    """Produce one appeal row while retaining task records as atomic objects."""
    if df is None or df.empty:
        return pd.DataFrame(columns=[] if df is None else df.columns)
    work = df.copy()
    work["id"] = work["id"].map(normalize_id)
    task_field_names = ("task_id", "task_name", "task_status", "task_desc", "task_result", "exec_dept")
    task_columns = {
        column for column in task_field_names
        if column in work
    }
    dialog_columns = {
        column for column in ("msg_pprb_chat", "msg_crm_chat", "msg_sc_chat")
        if column in work
    }
    rows = []
    for appeal_id, group in work.groupby("id", sort=False):
        row = {
            column: _first_nonempty(group[column])
            for column in work.columns
            if column not in task_columns and column not in dialog_columns and column != "tasks"
        }
        row["id"] = appeal_id
        for column in dialog_columns:
            row[column] = _join_fragments(group[column])
        task_records: List[Dict[str, Optional[str]]] = []
        seen_tasks = set()
        for _, source_row in group.iterrows():
            existing = source_row.get("tasks")
            sources = existing if isinstance(existing, list) else [None]
            for existing_record in sources:
                record = {}
                for column in task_field_names:
                    raw = existing_record.get(column) if isinstance(existing_record, dict) else source_row.get(column)
                    if isinstance(raw, (list, dict, tuple, set)):
                        raw = None
                    record[column] = str(raw).strip() if raw is not None and pd.notna(raw) and str(raw).strip() else None
                if not any(record.values()):
                    continue
                fingerprint = tuple(record.items())
                if fingerprint not in seen_tasks:
                    seen_tasks.add(fingerprint)
                    task_records.append(record)
        row["tasks"] = task_records
        for column in task_field_names:
            values = [record[column] for record in task_records if record.get(column)]
            row[column] = values if len(values) > 1 else (values[0] if values else None)
        row["description"] = (
            row.get("msg_pprb_chat")
            or row.get("msg_crm_chat")
            or row.get("msg_sc_chat")
            or row.get("app_content")
            or row.get("short_description")
        )
        rows.append(row)
    return _standardize_df_columns(pd.DataFrame(rows))


def merge_hydration_frames(base: pd.DataFrame, dialogs: Optional[pd.DataFrame], tasks: Optional[pd.DataFrame]) -> pd.DataFrame:
    """Aggregate each one-to-many relation before attaching it to base rows."""
    work = base.copy()
    key_columns = ["source_year", "app_row_id"]
    if dialogs is not None and not dialogs.empty:
        dialog_rows = []
        for key, group in dialogs.groupby(key_columns, sort=False, dropna=False):
            row = dict(zip(key_columns, key if isinstance(key, tuple) else (key,)))
            for column in ("msg_pprb_chat", "msg_crm_chat", "msg_sc_chat"):
                if column in group:
                    row[column] = _join_fragments(group[column])
            dialog_rows.append(row)
        work = work.merge(pd.DataFrame(dialog_rows), on=key_columns, how="left", validate="many_to_one")
    if tasks is not None and not tasks.empty:
        task_rows = []
        for key, group in tasks.groupby(key_columns, sort=False, dropna=False):
            row = dict(zip(key_columns, key if isinstance(key, tuple) else (key,)))
            records = []
            seen = set()
            for _, source in group.iterrows():
                record = {
                    column: (str(source.get(column)).strip() if pd.notna(source.get(column)) and str(source.get(column)).strip() else None)
                    for column in ("task_id", "task_name", "task_status", "task_desc", "task_result", "exec_dept")
                }
                fingerprint = tuple(record.items())
                if any(record.values()) and fingerprint not in seen:
                    seen.add(fingerprint)
                    records.append(record)
            row["tasks"] = records
            task_rows.append(row)
        work = work.merge(pd.DataFrame(task_rows), on=key_columns, how="left", validate="many_to_one")
    return normalize_hydrated_appeals(work)


def fetch_appeals_by_ids(candidate_ids: Sequence[Any]) -> pd.DataFrame:
    ids = list(dict.fromkeys(normalize_id(value) for value in candidate_ids if normalize_id(value)))
    if not ids:
        return pd.DataFrame()

    logger.info("Hydration batch started: candidates=%s", len(ids))
    started = time.monotonic()

    def _hydrate(conn: Any) -> Tuple[
        Optional[pd.DataFrame], Optional[pd.DataFrame], Optional[pd.DataFrame]
    ]:
        base = _run_sql_on_connection(
            conn, build_hydration_sql(ids), label="hydration base",
        )
        if base is None or base.empty:
            return base, None, None
        ids_by_year = _related_ids_by_year(base)
        dialogs = (
            _run_sql_on_connection(
                conn, build_dialog_hydration_sql(ids_by_year), label="hydration dialogs",
            )
            if ids_by_year else None
        )
        tasks = (
            _run_sql_on_connection(
                conn, build_task_hydration_sql(ids_by_year), label="hydration tasks",
            )
            if ids_by_year else None
        )
        return base, dialogs, tasks

    base, dialogs, tasks = db.run(_hydrate)
    logger.info(
        "Hydration DB phase finished: candidates=%s elapsed=%.2fs",
        len(ids), time.monotonic() - started,
    )
    if base is None or base.empty:
        return pd.DataFrame()

    result = merge_hydration_frames(base, dialogs, tasks)
    logger.info("Hydration normalized: unique=%s", len(result))
    return result
