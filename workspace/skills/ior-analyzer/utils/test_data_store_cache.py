from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd


UTILS_DIR = Path(__file__).resolve().parent
REPO_ROOT = UTILS_DIR.parents[3]
if str(UTILS_DIR) not in sys.path:
    sys.path.insert(0, str(UTILS_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SPEC = importlib.util.spec_from_file_location("ior_cache_data_store", UTILS_DIR / "data_store.py")
assert SPEC is not None and SPEC.loader is not None
data_store = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(data_store)


def _provider(*, opens: bool = True, result: dict | None = None) -> MagicMock:
    provider = MagicMock()
    provider.open_cache.return_value = opens
    provider.query_sql.return_value = result or {
        "status": "success",
        "columns": ["incdnt_id", "amount"],
        "rows": [(1, 10.5), (2, 20.0)],
        "row_count": 2,
    }
    return provider


class TestNanobotCacheStore(unittest.TestCase):
    def setUp(self) -> None:
        self.env = patch.dict(os.environ, {}, clear=False)
        self.env.start()
        os.environ.pop("IOR_DATA_BACKEND", None)
        os.environ.pop("DATA_BACKEND", None)
        os.environ.pop("IOR_CACHE_WAIT_SECONDS", None)
        os.environ.pop("IOR_CACHE_POLL_INTERVAL", None)
        data_store.reset_data_store()

    def tearDown(self) -> None:
        data_store.reset_data_store()
        self.env.stop()

    def test_default_backend_uses_gateway_cache(self) -> None:
        provider = _provider()
        with patch.object(data_store, "build_cache_provider", return_value=provider), patch.object(
            data_store.time, "sleep"
        ) as sleep:
            store = data_store.get_data_store()

        self.assertIsInstance(store, data_store.NanobotCacheStore)
        provider.open_cache.assert_called_once_with()
        sleep.assert_not_called()
        provider.refresh.assert_not_called()

    def test_default_wait_settings(self) -> None:
        provider = _provider()
        with patch.object(data_store.NanobotCacheStore, "_wait_for_cache") as wait:
            data_store.NanobotCacheStore(provider=provider)

        wait.assert_called_once_with(60.0, 1.0)

    def test_wait_settings_can_be_overridden_by_environment(self) -> None:
        os.environ["IOR_CACHE_WAIT_SECONDS"] = "3.5"
        os.environ["IOR_CACHE_POLL_INTERVAL"] = "0.25"
        with patch.object(data_store.NanobotCacheStore, "_wait_for_cache") as wait:
            data_store.NanobotCacheStore(provider=_provider())

        wait.assert_called_once_with(3.5, 0.25)

    def test_cache_becomes_available_after_retries(self) -> None:
        provider = _provider()
        provider.open_cache.side_effect = [False, False, True]
        with patch.object(
            data_store.time, "monotonic", side_effect=[0.0, 0.0, 1.0, 2.0]
        ), patch.object(data_store.time, "sleep") as sleep:
            store = data_store.NanobotCacheStore(
                provider=provider, wait_seconds=5, poll_interval=1
            )

        self.assertIsInstance(store, data_store.NanobotCacheStore)
        self.assertEqual(provider.open_cache.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        provider.refresh.assert_not_called()

    def test_cache_wait_timeout(self) -> None:
        provider = _provider(opens=False)
        with patch.object(
            data_store.time, "monotonic", side_effect=[0.0, 0.0, 1.0, 2.0]
        ), patch.object(data_store.time, "sleep") as sleep:
            with self.assertRaisesRegex(
                RuntimeError, "Start gateway and wait for initial synchronization"
            ):
                data_store.NanobotCacheStore(
                    provider=provider, wait_seconds=2, poll_interval=1
                )

        self.assertEqual(provider.open_cache.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        provider.refresh.assert_not_called()

    def test_unexpected_open_error_is_not_retried(self) -> None:
        provider = _provider()
        provider.open_cache.side_effect = ValueError("corrupt cache")
        with patch.object(data_store.time, "sleep") as sleep:
            with self.assertRaisesRegex(ValueError, "corrupt cache"):
                data_store.NanobotCacheStore(provider=provider)

        provider.open_cache.assert_called_once_with()
        sleep.assert_not_called()
        provider.refresh.assert_not_called()

    def test_missing_cache_has_no_local_or_greenplum_fallback(self) -> None:
        provider = _provider(opens=False)
        with patch.object(data_store, "build_cache_provider", return_value=provider), patch.object(
            data_store, "LocalDuckDBStore", side_effect=AssertionError("local DuckDB fallback")
        ) as local, patch.object(
            data_store, "GreenplumStore", side_effect=AssertionError("Greenplum fallback")
        ) as greenplum, patch.object(
            data_store.time, "monotonic", side_effect=[0.0, 0.0, 60.0]
        ), patch.object(data_store.time, "sleep"):
            with self.assertRaisesRegex(
                RuntimeError, "Start gateway and wait for initial synchronization"
            ):
                data_store.get_data_store()

        local.assert_not_called()
        greenplum.assert_not_called()
        provider.refresh.assert_not_called()

    def test_cache_provider_construction_error_is_not_hidden(self) -> None:
        with patch.object(
            data_store, "build_cache_provider", side_effect=PermissionError("cache directory")
        ):
            with self.assertRaisesRegex(PermissionError, "cache directory"):
                data_store.get_data_store()

    def test_provider_result_is_converted_to_dataframe(self) -> None:
        provider = _provider()
        store = data_store.NanobotCacheStore(provider=provider)
        frame = store.query_sql("SELECT * FROM incidents WHERE incdnt_id = ?", [1])

        pd.testing.assert_frame_equal(
            frame,
            pd.DataFrame({"incdnt_id": [1, 2], "amount": [10.5, 20.0]}),
        )
        provider.query_sql.assert_called_once_with(
            "SELECT * FROM incidents WHERE incdnt_id = ?", [1]
        )

    def test_cache_uses_schema_qualified_greenplum_names(self) -> None:
        store = data_store.NanobotCacheStore(provider=_provider())
        self.assertEqual(store.tables, data_store.GREENPLUM_TABLES)
        self.assertTrue(
            all(name.startswith(f"{data_store.GP_SCHEMA}.") for name in store.tables.values())
        )
        self.assertTrue(all(not name.startswith("d6_") for name in store.tables.values()))

    def test_greenplum_requires_explicit_skill_specific_selector(self) -> None:
        sentinel = object()
        os.environ["IOR_DATA_BACKEND"] = "greenplum"
        with patch.object(data_store, "GreenplumStore", return_value=sentinel), patch.object(
            data_store.time, "sleep"
        ) as sleep:
            self.assertIs(data_store.get_data_store(), sentinel)
        sleep.assert_not_called()

    def test_local_duckdb_requires_explicit_selector(self) -> None:
        sentinel = object()
        os.environ["IOR_DATA_BACKEND"] = "local_duckdb"
        with patch.object(data_store, "LocalDuckDBStore", return_value=sentinel), patch.object(
            data_store.time, "sleep"
        ) as sleep:
            self.assertIs(data_store.get_data_store(), sentinel)
        sleep.assert_not_called()

    def test_generic_data_backend_is_ignored(self) -> None:
        provider = _provider()
        os.environ["DATA_BACKEND"] = "greenplum"
        with patch.object(data_store, "build_cache_provider", return_value=provider):
            self.assertIsInstance(data_store.get_data_store(), data_store.NanobotCacheStore)

    def test_project_registration_contains_all_ior_tables(self) -> None:
        from config import SETTINGS
        from lib.core.project_settings import SkillSettings
        from lib.core.skill_registration import register_skill_from_config
        from lib.services.table_registry import TableRegistry

        raw = SETTINGS["skills"]["ior_analyzer"]
        validated = SkillSettings.model_validate(raw)
        registry = TableRegistry()
        register_skill_from_config(
            "ior_analyzer", validated.model_dump(exclude_none=True), registry=registry
        )

        self.assertEqual(set(registry.table_names()), set(data_store.GREENPLUM_TABLES.values()))
        expected_tracking = {
            data_store.GREENPLUM_TABLES["ior"]: "incdnt_last_validate_dttm",
            data_store.GREENPLUM_TABLES["status"]: "stts_chng_action_dttm",
            data_store.GREENPLUM_TABLES["recovery"]: "recovery_creation_dttm",
            data_store.GREENPLUM_TABLES["financial_impact"]: "fin_impact_creation_dttm",
            data_store.GREENPLUM_TABLES["nonfinancial_impact"]: "nonfin_impact_sid",
        }
        self.assertEqual(
            {table: registry.tracking_column_for(table) for table in registry.table_names()},
            expected_tracking,
        )

    def test_gateway_sync_service_receives_all_ior_tables(self) -> None:
        from config import SETTINGS
        from lib.core.application_context import _make_sync_services
        from lib.core.skill_registration import register_skill_from_config
        from lib.services import table_registry as registry_module
        from lib.services.table_registry import TableRegistry

        registry = TableRegistry()
        register_skill_from_config(
            "ior_analyzer", SETTINGS["skills"]["ior_analyzer"], registry=registry
        )

        class ConfigService:
            @staticmethod
            def settings_section(name: str):
                if name == "channels":
                    return {"postgres": {"dsn": "postgresql://unused-for-construction"}}
                if name == "gateway":
                    return {
                        "cache": {"local_path": cache_dir},
                        "sync": {
                            "poll_interval_sec": 1,
                            "max_queue_size": 1,
                            "reconnect_backoff_sec": 1,
                            "reconnect_backoff_max_sec": 1,
                            "full_resync_every": 0,
                        },
                    }
                return {}

        class FakeSync:
            def __init__(self, **kwargs):
                self._tables = list(kwargs["tables"])

        class FakeStore:
            def __init__(self, **kwargs):
                self.tables = list(kwargs.get("tables") or [])

        with tempfile.TemporaryDirectory() as cache_dir, patch.object(
            registry_module, "table_registry", registry
        ), patch.dict(
            sys.modules,
            {
                "lib.services.pg_duckdb_sync_service": SimpleNamespace(
                    PgDuckDbSyncService=FakeSync
                ),
                "lib.services.duckdb_cache_store": SimpleNamespace(
                    DuckDbCacheStore=FakeStore
                ),
            },
        ):
            ctx = SimpleNamespace(
                config_service=ConfigService(),
                config=SimpleNamespace(workspace_path=str(REPO_ROOT / "workspace")),
                db_logging_service=None,
            )
            sync, _store = _make_sync_services(ctx)

        self.assertIsNotNone(sync)
        self.assertEqual(set(data_store.GREENPLUM_TABLES.values()), set(sync._tables))


if __name__ == "__main__":
    unittest.main()
