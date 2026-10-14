"""PG-бэкенд для standalone-CLI ``audit_analyzer`` (nanobot_bugfix_stutter).

Зачем
-----
CLI навыка работает вне gateway и читает данные из DuckDB-снимка
``~/.cache/nanobot/duckdb/cache.duckdb``. DuckDB — однопользовательская БД:
пока файл держит gateway, **любой другой процесс не может его открыть**,
ни на чтение. На Windows это выглядит так:

    duckdb.IOException: IO Error: Cannot open file "...cache.duckdb":
    ���роцесс не может получить доступ к файлу, так как этот файл занят
    другим процессом. File is already open in ... (PID ...)

Из-за этого CLI сообщал «DuckDB-кеш не найден ... запустите его
(python gateway.py)» — то есть вводил в заблуждение: кэш есть, он просто
занят работающим gateway, который и должен его публиковать.

Что делает этот модуль
----------------------
Даёт ``query_sql``-совместимый провайдер поверх **PostgreSQL** — источника
истины для витрин ``oarb.*``. CLI переключается на него автоматически,
когда снимок DuckDB занят (см. ``scripts/cli.py::_open_db``).

Ограничения осознанные: ``search_vector`` (FAISS) и ``explain`` через
DuckDB недоступны — они требуют локального индекса. Для агрегатов,
predefined-скриптов и ``generated_sql`` (а это и есть основные режимы
CLI) PG-путь эквивалентен: те же таблицы, тот же SQL.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _db():
    """``workspace.utils.db`` — общий пул и ``resolve_dsn()``."""
    from utils import db as _shared

    return _shared


class PostgresQueryProvider:
    """Минимальный ``query_sql``-провайдер поверх PostgreSQL.

    Повторяет контракт ``CacheProvider.query_sql``
    (``lib/services/cache_provider.py:148``): возвращает
    ``{status, row_count, columns, rows}``. Метод ``open_cache()`` оставлен
    для совместимости с путём загрузки CLI — здесь он проверяет живое
    соединение, а не наличие файла на диске.
    """

    backend_name = "postgres"

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn
        self._checked = False

    # -- открытие -------------------------------------------------------
    def _resolve_dsn(self) -> str:
        if self._dsn:
            return self._dsn
        shared = _db()
        resolver = getattr(shared, "resolve_dsn", None)
        dsn = resolver() if callable(resolver) else ""
        if not dsn:
            raise RuntimeError(
                "PG-бэкенд недоступен: не задан DSN. Ожидается "
                "channels.postgres.dsn (или DATABASE_URL) в .secrets.env."
            )
        self._dsn = dsn
        return dsn

    def open_cache(self) -> bool:
        """Проверить живое соединение (аналог ``open_cache`` для кэша)."""
        if self._checked:
            return True
        try:
            self._resolve_dsn()
            _db().fetchval("SELECT 1")
        except Exception as exc:  # noqa: BLE001 — это probe, не работа
            logger.warning("[audit_analyzer/pg] соединение недоступно: %s", exc)
            return False
        self._checked = True
        return True

    # -- чтение ---------------------------------------------------------
    def query_sql(self, sql: str, params: list[Any] | None = None) -> dict[str, Any]:
        shared = _db()
        args = tuple(params or ())
        try:
            rows = shared.fetch(sql, *args) if args else shared.fetch(sql)
        except Exception as exc:  # noqa: BLE001 — контракт возвращает error
            return {"status": "error", "error": str(exc), "rows": [],
                    "columns": [], "row_count": 0}
        columns = list(rows[0].keys()) if rows else []
        return {
            "status": "success",
            "row_count": len(rows),
            "columns": columns,
            "rows": rows,
        }

    def get_schema(
        self,
        schema_name: str | None = None,
        table_names: list[str] | None = None,
    ) -> dict[str, Any]:
        shared = _db()
        sql = (
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = %s"
        )
        args: tuple[Any, ...] = (schema_name or "public",)
        if table_names:
            sql += " AND table_name = ANY(%s)"
            args = args + (list(table_names),)
        sql += " ORDER BY table_name, ordinal_position"
        try:
            rows = shared.fetch(sql, *args) if len(args) > 1 else shared.fetch(sql)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[audit_analyzer/pg] get_schema: %s", exc)
            return {}
        out: dict[str, Any] = {}
        for r in rows:
            out.setdefault(r["table_name"], []).append(r["column_name"])
        return out

    def explain(self, sql: str) -> dict[str, Any]:
        """Синтаксическая проверка: PREPARE на сервере PG."""
        try:
            _db().execute(f"PREPARE _audit_analyzer_check AS {sql}")
        except Exception as exc:  # noqa: BLE001
            return {"valid": False, "error": str(exc)}
        finally:
            try:
                _db().execute("DEALLOCATE _audit_analyzer_check")
            except Exception:  # noqa: BLE001,S110 — PREPARE мог не создаться
                pass
        return {"valid": True, "plan": []}

    def search_vector(self, *args: Any, **kwargs: Any) -> list:
        """Векторный поиск требует локального FAISS-индекса — недоступен в PG."""
        raise NotImplementedError(
            "Семантический поиск требует локального FAISS-индекса и доступен "
            "только когда DuckDB-снимок не занят gateway. Для агрегатов и "
            "скриптов используется PG-бэкенд."
        )

    def close(self) -> None:
        # Пул общий с gateway-процессом — закрывать его здесь нельзя.
        return None
