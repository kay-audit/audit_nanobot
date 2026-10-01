"""Optional, lazy Spark analysis backend.  It never executes an action."""
from __future__ import annotations

import asyncio
import threading
from typing import Any


class SparkInfraError(RuntimeError):
    pass


class SparkBackend:
    _session: Any = None
    _lock = threading.Lock()

    @classmethod
    def available(cls) -> bool:
        try:
            import pyspark  # noqa: F401
            return True
        except ImportError:
            return False

    @classmethod
    def session(cls) -> Any:
        if not cls.available():
            raise SparkInfraError("PySpark is not installed")
        with cls._lock:
            if cls._session is None:
                from pyspark.sql import SparkSession
                cls._session = SparkSession.builder.appName("nanobot-sql-assistant-validate").getOrCreate()
            return cls._session

    @classmethod
    def _analyze_sync(cls, sql: str) -> dict[str, Any]:
        try:
            frame = cls.session().sql(sql)
            analyzed = frame._jdf.queryExecution().analyzed().toString()
            return {"status": "valid", "plan": analyzed}
        except SparkInfraError:
            raise
        except Exception as exc:
            return {"status": "invalid", "error": str(exc)}

    @classmethod
    async def analyze(cls, sql: str, *, timeout_sec: float = 60.0) -> dict[str, Any]:
        if not cls.available():
            return {"status": "unavailable", "error": "PySpark is not installed"}
        try:
            return await asyncio.wait_for(asyncio.to_thread(cls._analyze_sync, sql), timeout=timeout_sec)
        except asyncio.TimeoutError:
            return {"status": "timeout", "error": f"Spark analyze exceeded {timeout_sec:g}s"}
        except SparkInfraError as exc:
            return {"status": "unavailable", "error": str(exc)}

    @classmethod
    def _columns_sync(cls, table_name: str) -> list[dict[str, str]]:
        return [{"name": field.name, "data_type": field.dataType.simpleString()} for field in cls.session().table(table_name).schema.fields]

    @classmethod
    async def list_columns(cls, table_name: str, *, timeout_sec: float = 30.0) -> dict[str, Any]:
        if not cls.available():
            return {"status": "unavailable", "columns": [], "error": "PySpark is not installed"}
        try:
            columns = await asyncio.wait_for(asyncio.to_thread(cls._columns_sync, table_name), timeout=timeout_sec)
            return {"status": "ok", "columns": columns}
        except asyncio.TimeoutError:
            return {"status": "timeout", "columns": [], "error": f"Spark catalog exceeded {timeout_sec:g}s"}
        except Exception as exc:
            return {"status": "unavailable", "columns": [], "error": str(exc)}

