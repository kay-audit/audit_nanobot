from __future__ import annotations
import sys
from pathlib import Path

_FILE_PATH = Path(__file__).resolve()
_SKILL_DIR = _FILE_PATH.parent
while _SKILL_DIR.parent != _SKILL_DIR:
    if (_SKILL_DIR / "SKILL.md").exists() or _SKILL_DIR.name == "ior-analyzer":
        break
    _SKILL_DIR = _SKILL_DIR.parent

_SCRIPTS_DIR = _SKILL_DIR / "scripts"
_UTILS_DIR = _SKILL_DIR / "utils"

for _dir in (_SKILL_DIR, _SCRIPTS_DIR, _UTILS_DIR):
    _sdir = str(_dir)
    if _sdir not in sys.path:
        sys.path.insert(0, _sdir)


import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)


@dataclass
class ColumnSchema:
    name: str
    type: str
    filled_pct: Optional[float] = None
    description: Optional[str] = None      # потом - из metastore comments
    enum_values: Optional[list[str]] = None  # для категориальных колонок


@dataclass
class TableSchema:
    name: str                           # короткое имя (d6_base_of_knowledge_ior)
    full_name: str                      # physical backend table name
    description: str
    row_count: int
    columns: list[ColumnSchema] = field(default_factory=list)
    foreign_keys: list[dict] = field(default_factory=list)

    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def to_llm_snippet(self, verbose: bool = False) -> str:
        """Один table-блок в формате, который понимает LLM."""
        lines = [f"### {self.full_name} ({self.row_count:,} rows)",
                 f"{self.description}",
                 "columns:"]
        for c in self.columns:
            line = f"  * {c.name}: {c.type}"
            if c.filled_pct is not None and c.filled_pct < 50:
                line += f"  (filled {c.filled_pct:.0f}%)"
            if c.description:
                line += f" - {c.description}"
            if c.enum_values:
                vals = ", ".join(f"'{v}'" for v in c.enum_values[:12])
                more = "" if len(c.enum_values) <= 12 else f" (+{len(c.enum_values)-12} еще)"
                line += f"  ENUM: [{vals}{more}]"
            lines.append(line)
        if self.foreign_keys:
            lines.append("joins:")
            for fk in self.foreign_keys:
                line = f"  * {fk['column']} -> {fk['references']}"
                if fk.get("note"):
                    line += f" ({fk['note']})"
                lines.append(line)
        return "\n".join(lines)


@dataclass
class Schema:
    tables: dict[str, TableSchema] = field(default_factory=dict)
    common_filters: dict = field(default_factory=dict)
    ready_aggregates_in_main: list[dict] = field(default_factory=list)

    def get(self, name: str) -> Optional[TableSchema]:
        return self.tables.get(name)

    def table_names(self) -> list[str]:
        return list(self.tables.keys())

    def to_llm_snippet(self) -> str:
        """Полная схема для system prompt'а planner'а."""
        parts = [
            "# СХЕМА БАЗЫ ЗНАНИЙ ИОР",
            "",
            f"# ДОСТУПНО {len(self.tables)} ТАБЛИЦ: " +
            ", ".join(self.tables.keys()),
            "# ⚠️  В БЗ НЕТ таблицы сотрудников/persons, HR-данных, KPI, оргструктуры "
            "уровня позиций.",
            "# ⚠️  Если юзер спрашивает про сотрудников / отвественных по имени - "
            "используй поле incdnt_responsible_person_sid в main-таблице "
            "как идентификатор. Для расшифровки имени нужна внешняя система "
            "\"(в БЗ её нет) - в этом случае верни ask_user.\"",
            "",
        ]
        for t in self.tables.values():
            parts.append(t.to_llm_snippet())
            parts.append("")
        if self.common_filters:
            parts.append("# Типичные фильтры:")
            for k, v in self.common_filters.items():
                col = v.get("column") or ", ".join(v.get("columns", []))
                note = v.get("note", "")
                parts.append(f"  * {k}: {col} - {note}")
            parts.append("")
        if self.ready_aggregates_in_main:
            parts.append("# Готовые агрегаты в main-таблице ")
            parts.append("  (JOIN с fin_impact для них НЕ нужен):")
            for item in self.ready_aggregates_in_main:
                for col, desc in item.items():
                    parts.append(f"  * {col} - {desc}")
            parts.append("")
        return "\n".join(parts)


_SCHEMA: Optional[Schema] = None


def _yaml_path() -> Path:
    return Path(__file__).parent / "kb_schema.yaml"


def reload_schema() -> Schema:
    """Перечитать YAML с диска. Возвращает свежий Schema."""
    global _SCHEMA
    path = _yaml_path()
    if not path.exists():
        logger.warning("[Schema] YAML %s не найден - пустая схема", path)
        _SCHEMA = Schema()
        return _SCHEMA

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    tables = {}
    for name, data in (raw.get("tables") or {}).items():
        cols = [
            ColumnSchema(
                name=c["name"],
                type=c.get("type", "string"),
                filled_pct=c.get("filled_pct"),
                description=c.get("description"),
                enum_values=c.get("enum_values"),
            )
            for c in (data.get("columns") or [])
        ]
        tables[name] = TableSchema(
            name=name,
            full_name=data.get("full_name", name),
            description=data.get("description", ""),
            row_count=data.get("row_count", 0),
            columns=cols,
            foreign_keys=data.get("foreign_keys") or [],
        )
    _SCHEMA = Schema(
        tables=tables,
        common_filters=raw.get("common_filters") or {},
        ready_aggregates_in_main=raw.get("ready_aggregates_in_main") or [],
    )
    logger.info("[Schema] Загружено таблиц: %d (из %s)",
                len(tables), path.name)
    return _SCHEMA


def get_schema() -> Schema:
    global _SCHEMA
    if _SCHEMA is None:
        reload_schema()
    return _SCHEMA


# Эвристики автодетекта enum-колонок.
#
# Стратегия: НЕ держим список таблиц/колонок. Идём по схеме и берём
# КАЖДУЮ колонку которая «выглядит как категориальная». Это работает
# для любой новой таблицы — добавил YAML -> нашлось автоматом.
#
# Имена-маркеры: колонка КАНДИДАТ если содержит один из суффиксов
_ENUM_NAME_SUFFIXES = (
    "_name", "_code", "_status", "_type", "_kind", "_flag",
    "_category", "_cat", "_class", "_grade", "_level", "_lvl",
    "_state", "_stage", "_phase", "_role", "_action", "_method",
    "_mode", "_origin", "_source", "_priority", "_severity",
)

# Имена-исключения: колонка НЕ кандидат если она явно идентификатор/число/дата
_ENUM_NAME_EXCLUDE_SUFFIXES = (
    "_id", "_sid", "_uid", "_num", "_no",
    "_dt", "_dttm", "_date", "_time", "_ts",
    "_amt", "_sum", "_total", "_count", "_qty", "_pct", "_rate",
)
# Имена-исключения: точные имена которые не еnum
_ENUM_NAME_EXCLUDE_EXACT = {
    "incdnt_summary_descr_txt", "incdnt_descr", "incdnt_short_descr_txt",
    "comments", "note", "notes", "description", "title",
}


def _is_enum_candidate(col) -> bool:
    """Эвристика: колонка похожа на категориальную (фиксированный список
    значений), значит её ENUM имеет смысл подгрузить."""
    name = col.name.lower()
    if name in _ENUM_NAME_EXCLUDE_EXACT:
        return False
    if any(name.endswith(s) for s in _ENUM_NAME_EXCLUDE_SUFFIXES):
        return False
    # Только строковые колонки имеют смысл как enum
    col_type = (col.type or "").lower()
    if not any(t in col_type for t in ("string", "varchar", "text", "char")):
        return False
    # Очень редко заполненные - скорее всего мусорные
    if col.filled_pct is not None and col.filled_pct < 1:
        return False
    # Длинные текстовые поля (summary, descr) - не enum
    if any(p in name for p in ("descr", "txt_", "_txt", "summary", "comment")):
        return False
    # Имя содержит маркер категориальности?
    if any(s in name for s in _ENUM_NAME_SUFFIXES):
        return True
    return False


def auto_detect_enum_candidates() -> list[tuple[str, str]]:
    """Возвращает list[(table, column)] - все строковые колонки которые
    выглядят как enum по эвристикам имени/типа. Никаких хардкод-списков.
    """
    schema = get_schema()
    out: list[tuple[str, str]] = []
    for tbl in schema.tables.values():
        for col in tbl.columns:
            if _is_enum_candidate(col):
                out.append((tbl.name, col.name))
    return out


_ENRICHED_KEYS: set[tuple[str, str]] = set()
# Лимит уникальных значений - если distinct returns >= этого, то это НЕ enum
# (например, имена клиентов, ID-подобные строки), не сохраняем.
_ENUM_MAX_DISTINCT = 50


def enrich_one_column(store, table: str, column: str,
                      max_values: int = _ENUM_MAX_DISTINCT) -> Optional[list[str]]:
    """Загружает SELECT DISTINCT для одной колонки и обновляет enum_values
    в Schema. Идемпотентно. Возвращает список значений или None.

    Если distinct >= max_values, считаем что это не enum - не сохраняем.
    """
    key = (table, column)
    if key in _ENRICHED_KEYS:
        # Уже пробовали; возвращаем что есть (может быть None)
        schema = get_schema()
        tbl = schema.get(table)
        if tbl:
            col = next((c for c in tbl.columns if c.name == column), None)
            if col:
                return col.enum_values
        return None

    schema = get_schema()
    tbl = schema.get(table)
    if tbl is None:
        return None
    col = next((c for c in tbl.columns if c.name == column), None)
    if col is None:
        return None

    try:
        # Запрашиваем max+1 чтобы понять «хвост обрезан - слишком много»
        values = store.fetch_distinct_values(
            table=table, column=column, max_values=max_values + 1,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[Schema] enrich %s.%s упал: %s", table, column, e)
        _ENRICHED_KEYS.add(key)
        return None

    _ENRICHED_KEYS.add(key)

    if not values:
        return None
    if len(values) > max_values:
        # Это не enum - слишком много уникальных. Не сохраняем.
        logger.debug("[Schema] %s.%s: >%d distinct, не enum - skip",
                     table, column, max_values)
        return None

    col.enum_values = values
    logger.info("[Schema] %s.%s <- %d значений из БД: %s",
                table, column, len(values),
                ", ".join(repr(v) for v in values[:5]) +
                ("..." if len(values) > 5 else ""))
    return values


def enrich_schema_with_real_enums(store, max_columns: int = 30) -> int:
    """Запускается ОДИН раз при старте - обогащает schema актуальными
    enum-значениями для ВСЕХ автодетектированных кандидатов из ЛЮБЫХ
    таблиц. Никакого хардкодного списка - добавил таблицу в YAML,
    она автоматически попадает в обогащение.

    Cap по max_columns чтобы не делать сотни SELECT DISTINCT за раз.

    Возвращает число успешно обновлённых колонок.
    """
    candidates = auto_detect_enum_candidates()
    if len(candidates) > max_columns:
        logger.info("[Schema] автодетекст нашёл %d enum-кандидатов, "
                    "ограничиваю до %d", len(candidates), max_columns)
        candidates = candidates[:max_columns]
    n_updated = 0
    for table, column in candidates:
        if enrich_one_column(store, table, column) is not None:
            n_updated += 1
    return n_updated
