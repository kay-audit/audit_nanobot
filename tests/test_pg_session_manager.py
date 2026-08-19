from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add workspace to sys.path so utils.db can be imported
_project_root = Path(__file__).resolve().parent.parent
_workspace_path = str(_project_root / "workspace")
if _workspace_path not in sys.path:
    sys.path.insert(0, _workspace_path)


@pytest.fixture(autouse=True)
def mock_db_and_psycopg():
    """Mock utils.db and psycopg2.extras before importing pg_session_manager."""
    with (
        patch.dict("sys.modules"),
        patch("psycopg2.extras.Json", lambda x: x),
        patch("psycopg2.extras.execute_values"),
    ):
        # Force fresh import of pg_session_manager to pick up mocked deps
        sys.modules.pop("lib.session.pg_session_manager", None)
        sys.modules.pop("lib.session", None)

        import types

        utils_db = types.ModuleType("utils.db")
        utils_db.DB_RETRYABLE_ERRORS = (Exception,)
        utils_db.transaction = MagicMock()
        sys.modules["utils.db"] = utils_db
        sys.modules["utils"] = types.ModuleType("utils")

        from lib.session.pg_session_manager import PGSessionManager

        def _make(**kwargs):
            defaults = {
                "messages_table": kwargs.pop("messages_table", "agent_session_messages"),
                "meta_table": kwargs.pop("meta_table", "agent_session_meta"),
            }
            kwargs.setdefault("workspace", Path("/tmp/ws"))
            return PGSessionManager(workspace=kwargs.pop("workspace"), **defaults, **kwargs)

        yield _make


class TestPGSessionManagerPure:
    """Tests for pure (static) methods that don't need DB."""

    def test_validate_ident_valid(self):
        from lib.session.pg_session_manager import PGSessionManager

        PGSessionManager._validate_ident("public")  # no error
        PGSessionManager._validate_ident("session_meta")
        PGSessionManager._validate_ident("a1$b2")

    def test_validate_ident_invalid(self):
        from lib.session.pg_session_manager import PGSessionManager

        with pytest.raises(ValueError, match="Unsafe SQL identifier"):
            PGSessionManager._validate_ident("")
        with pytest.raises(ValueError, match="Unsafe SQL identifier"):
            PGSessionManager._validate_ident("table; DROP")
        with pytest.raises(ValueError, match="Unsafe SQL identifier"):
            PGSessionManager._validate_ident("a-b")

    def test_quote_simple(self):
        from lib.session.pg_session_manager import PGSessionManager

        assert PGSessionManager._quote("public.session_meta") == '"public"."session_meta"'

    def test_quote_single(self):
        from lib.session.pg_session_manager import PGSessionManager

        assert PGSessionManager._quote("session_meta") == '"session_meta"'

    def test_quote_invalid_raises(self):
        from lib.session.pg_session_manager import PGSessionManager

        with pytest.raises(ValueError):
            PGSessionManager._quote("public;.table")

    def test_session_payload(self):
        from lib.session.pg_session_manager import PGSessionManager, Session

        session = Session(
            key="test-key",
            messages=[{"role": "user", "content": "hi"}],
        )
        payload = PGSessionManager._session_payload(session)
        assert payload["key"] == "test-key"
        assert len(payload["messages"]) == 1
        assert "created_at" in payload
        assert "updated_at" in payload
        assert "metadata" in payload

    def test_init_custom_schema(self, mock_db_and_psycopg):
        mgr = mock_db_and_psycopg(
            workspace=Path("/tmp/ws"),
            schema="custom",
            messages_table="msgs",
            meta_table="meta",
        )
        assert '"custom"."msgs"' in mgr._fq_messages
        assert '"custom"."meta"' in mgr._fq_meta

    def test_init_sets_framework_contract(self, mock_db_and_psycopg, tmp_path):
        """Фреймворк (WebUI /api/sessions, read_session_metadata) требует
        sessions_dir/legacy_sessions_dir от менеджера сессий — регрессия
        AttributeError: 'PGSessionManager' object has no attribute 'sessions_dir'."""
        ws = tmp_path / "ws"
        mgr = mock_db_and_psycopg(workspace=ws)
        assert mgr.sessions_dir == (ws / "sessions").resolve()
        assert mgr.sessions_dir.is_dir()
        assert mgr.legacy_sessions_dir is not None
        assert hasattr(mgr, "read_session_metadata")
        assert callable(mgr.read_session_metadata)


class TestPGSessionManagerGetOrCreate:
    def test_returns_cached(self, mock_db_and_psycopg):
        from lib.session.pg_session_manager import Session

        mgr = mock_db_and_psycopg(workspace=Path("/tmp/ws"))
        session = Session(key="cached-key")
        mgr._cache["cached-key"] = session
        assert mgr.get_or_create("cached-key") is session

    @patch("lib.session.pg_session_manager.transaction")
    def test_creates_new_when_not_found(self, mock_trans, mock_db_and_psycopg):
        mgr = mock_db_and_psycopg(workspace=Path("/tmp/ws"))
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.__enter__.return_value = mock_cur
        mock_cur.fetchone.return_value = None  # no meta row
        mock_conn.cursor.return_value = mock_cur
        mock_trans.return_value.__enter__.return_value = mock_conn

        session = mgr.get_or_create("new-key")
        assert session.key == "new-key"
        assert mgr._cache["new-key"] is session


class TestPGSessionManagerSave:
    @patch("lib.session.pg_session_manager.transaction")
    @patch("lib.session.pg_session_manager.execute_values")
    def test_save_new_session(self, mock_exec_vals, mock_trans, mock_db_and_psycopg):
        from lib.session.pg_session_manager import Session

        mgr = mock_db_and_psycopg(workspace=Path("/tmp/ws"))
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.rowcount = 0  # UPDATE found no rows → will INSERT
        mock_conn.cursor = MagicMock(return_value=mock_cur)
        mock_trans.return_value.__enter__.return_value = mock_conn

        session = Session(key="s1", messages=[{"role": "user", "content": "hi"}])
        mgr.save(session)

        # Should call execute_values for messages
        mock_exec_vals.assert_called_once()

    @patch("lib.session.pg_session_manager.transaction")
    @patch("lib.session.pg_session_manager.execute_values")
    def test_save_existing_session(self, mock_exec_vals, mock_trans, mock_db_and_psycopg):
        from lib.session.pg_session_manager import Session

        mgr = mock_db_and_psycopg(workspace=Path("/tmp/ws"))
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.rowcount = 1  # UPDATE succeeded
        mock_conn.cursor.return_value = mock_cur
        mock_trans.return_value.__enter__.return_value = mock_conn

        session = Session(key="s1")
        mgr.save(session)
        # Should only call UPDATE, no INSERT
        insert_calls = [
            c for c in mock_cur.execute.call_args_list
            if "INSERT" in str(c)
        ]
        assert len(insert_calls) == 0

    @patch("lib.session.pg_session_manager.transaction")
    def test_save_db_error_raises(self, mock_trans, mock_db_and_psycopg):
        from lib.session.pg_session_manager import Session

        mgr = mock_db_and_psycopg(workspace=Path("/tmp/ws"))
        mock_trans.side_effect = Exception("DB down")

        session = Session(key="s1")
        # Ошибка БД пробрасывается — никакого JSONL-отката
        with pytest.raises(Exception, match="DB down"):
            mgr.save(session)


class TestPGSessionManagerDelete:
    @patch("lib.session.pg_session_manager.transaction")
    def test_delete_success(self, mock_trans, mock_db_and_psycopg):
        mgr = mock_db_and_psycopg(workspace=Path("/tmp/ws"))
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.rowcount = 1
        # cursor() called twice: first for messages DELETE, then for meta DELETE
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_trans.return_value.__enter__.return_value = mock_conn

        mgr._cache["del-key"] = "dummy"
        result = mgr.delete_session("del-key")
        assert result is True
        assert "del-key" not in mgr._cache

    @patch("lib.session.pg_session_manager.transaction")
    def test_delete_not_found(self, mock_trans, mock_db_and_psycopg):
        mgr = mock_db_and_psycopg(workspace=Path("/tmp/ws"))
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.rowcount = 0
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_trans.return_value.__enter__.return_value = mock_conn

        result = mgr.delete_session("non-existent")
        assert result is False


class TestPGSessionManagerList:
    @patch("lib.session.pg_session_manager.transaction")
    def test_list_sessions_empty(self, mock_trans, mock_db_and_psycopg):
        mgr = mock_db_and_psycopg(workspace=Path("/tmp/ws"))
        mock_conn = MagicMock()

        mock_cur1 = MagicMock()
        mock_cur1.description = [("session_key",), ("created_at",), ("updated_at",), ("metadata",)]
        mock_cur1.fetchall.return_value = []
        mock_cur1.__enter__.return_value = mock_cur1

        mock_conn.cursor.return_value.__enter__.return_value = mock_cur1
        mock_trans.return_value.__enter__.return_value = mock_conn

        result = mgr.list_sessions()
        assert result == []

    @patch("lib.session.pg_session_manager.transaction")
    def test_list_sessions_with_data(self, mock_trans, mock_db_and_psycopg):
        mgr = mock_db_and_psycopg(workspace=Path("/tmp/ws"))
        mock_conn = MagicMock()

        now = datetime(2024, 1, 1, 12, 0, 0)

        mock_cur1 = MagicMock()
        mock_cur1.__enter__.return_value = mock_cur1
        mock_cur1.description = [
            ("session_key",), ("created_at",), ("updated_at",), ("metadata",)
        ]
        mock_cur1.fetchall.return_value = [
            ("s1", now, now, '{"title": "Chat 1"}'),
        ]

        mock_cur2 = MagicMock()
        mock_cur2.__enter__.return_value = mock_cur2
        mock_cur2.__iter__.return_value = iter([("user", "Hello!")])

        mock_conn.cursor.side_effect = [mock_cur1, mock_cur2]
        mock_trans.return_value.__enter__.return_value = mock_conn

        result = mgr.list_sessions()
        assert len(result) == 1
        assert result[0]["key"] == "s1"
        assert result[0]["title"] == "Chat 1"
        assert result[0]["preview"] == "Hello!"


class TestPGSessionManagerFlushAll:
    def test_flush_all_empty(self, mock_db_and_psycopg):
        mgr = mock_db_and_psycopg(workspace=Path("/tmp/ws"))
        assert mgr.flush_all() == 0

    def test_flush_all_cached(self, mock_db_and_psycopg):
        from lib.session.pg_session_manager import Session

        mgr = mock_db_and_psycopg(workspace=Path("/tmp/ws"))
        mgr._cache = {"a": Session(key="a"), "b": Session(key="b")}
        with patch.object(mgr, "save") as mock_save:
            count = mgr.flush_all()
            assert count == 2
            assert mock_save.call_count == 2


class TestPGSessionManagerLoadLimit:
    """``_load_inner`` грузит только последние ``max_session_messages``
    сообщений по session_key (ORDER BY seq DESC LIMIT N + reverse).
    Это защита от раздувания context.messages при длинных диалогах /
    тяжёлых tool-результатах (audit_analyzer без LIMIT)."""

    @patch("lib.session.pg_session_manager.transaction")
    def test_default_limit_is_100(self, mock_trans, mock_db_and_psycopg):
        mgr = mock_db_and_psycopg(workspace=Path("/tmp/ws"))
        assert mgr._max_session_messages == 100, (
            "default max_session_messages должен быть 100"
        )

    @patch("lib.session.pg_session_manager.transaction")
    def test_custom_limit_in_constructor(self, mock_trans, mock_db_and_psycopg):
        mgr = mock_db_and_psycopg(workspace=Path("/tmp/ws"), max_session_messages=25)
        assert mgr._max_session_messages == 25

    @patch("lib.session.pg_session_manager.transaction")
    def test_load_filters_by_session_key(self, mock_trans, mock_db_and_psycopg):
        """Только сообщения для текущего session_key — никаких cross-session
        утечек (защита от случайного `WHERE session_key IS NULL` или
        отсутствия WHERE)."""
        mgr = mock_db_and_psycopg(workspace=Path("/tmp/ws"), max_session_messages=100)

        mock_conn = MagicMock()
        mock_cur_meta = MagicMock()
        mock_cur_meta.description = [
            ("session_key",), ("created_at",), ("updated_at",),
            ("last_consolidated",), ("metadata",),
        ]
        mock_cur_meta.fetchone.return_value = (
            "postgres:chat1", datetime(2024, 1, 1), datetime(2024, 1, 1),
            None, {},
        )
        mock_cur_meta.__enter__.return_value = mock_cur_meta

        mock_cur_msgs = MagicMock()
        mock_cur_msgs.description = [
            ("id",), ("session_key",), ("seq",), ("role",),
            ("content",), ("msg_timestamp",),
            ("tool_calls",), ("tool_call_id",), ("name",),
            ("reasoning_content",), ("thinking_blocks",),
            ("media",), ("cli_apps",), ("mcp_presets",),
            ("injected_event",), ("_command",), ("_channel_delivery",),
            ("created_at",),
        ]
        mock_cur_msgs.fetchall.return_value = [
            (1, "postgres:chat1", 0, "user", "hi", None,
             None, None, None, None, None, None, None, None,
             None, None, None, datetime(2024, 1, 1)),
        ]
        mock_cur_msgs.__enter__.return_value = mock_cur_msgs

        mock_conn.cursor.side_effect = [mock_cur_meta, mock_cur_msgs]
        mock_trans.return_value.__enter__.return_value = mock_conn

        mgr._load("postgres:chat1")
        # Должно быть 2 execute-вызова — meta + messages
        meta_sql = mock_cur_meta.execute.call_args.args[0]
        msgs_sql = mock_cur_msgs.execute.call_args.args[0]
        assert "WHERE session_key = %s" in meta_sql
        assert "WHERE session_key = %s" in msgs_sql
        # Сообщения: ORDER BY seq DESC LIMIT %s
        assert "ORDER BY seq DESC" in msgs_sql
        assert "LIMIT %s" in msgs_sql
        # Параметры: ("postgres:chat1", 100)
        params = mock_cur_msgs.execute.call_args.args[1]
        assert params == ("postgres:chat1", 100)

    @patch("lib.session.pg_session_manager.transaction")
    def test_load_keeps_most_recent_messages(self, mock_trans, mock_db_and_psycopg):
        """LIMIT N + DESC → последние N сообщений, затем reverse
        для хронологического порядка."""
        mgr = mock_db_and_psycopg(workspace=Path("/tmp/ws"), max_session_messages=3)

        mock_conn = MagicMock()
        mock_cur_meta = MagicMock()
        mock_cur_meta.description = [
            ("session_key",), ("created_at",), ("updated_at",),
            ("last_consolidated",), ("metadata",),
        ]
        mock_cur_meta.fetchone.return_value = (
            "k", datetime(2024, 1, 1), datetime(2024, 1, 1),
            None, {},
        )
        mock_cur_meta.__enter__.return_value = mock_cur_meta

        # Имитация: в БД 5 сообщений (seq 0..4), DESC LIMIT 3 вернёт seq=4,3,2.
        mock_cur_msgs = MagicMock()
        mock_cur_msgs.description = [
            ("id",), ("session_key",), ("seq",), ("role",),
            ("content",), ("msg_timestamp",),
            ("tool_calls",), ("tool_call_id",), ("name",),
            ("reasoning_content",), ("thinking_blocks",),
            ("media",), ("cli_apps",), ("mcp_presets",),
            ("injected_event",), ("_command",), ("_channel_delivery",),
            ("created_at",),
        ]
        mock_cur_msgs.fetchall.return_value = [
            (4, "k", 4, "user", "msg-4", None,
             None, None, None, None, None, None, None, None,
             None, None, None, datetime(2024, 1, 1)),
            (3, "k", 3, "user", "msg-3", None,
             None, None, None, None, None, None, None, None,
             None, None, None, datetime(2024, 1, 1)),
            (2, "k", 2, "user", "msg-2", None,
             None, None, None, None, None, None, None, None,
             None, None, None, datetime(2024, 1, 1)),
        ]
        mock_cur_msgs.__enter__.return_value = mock_cur_msgs

        mock_conn.cursor.side_effect = [mock_cur_meta, mock_cur_msgs]
        mock_trans.return_value.__enter__.return_value = mock_conn

        session = mgr._load("k")
        # DESC LIMIT 3 → [seq=4, seq=3, seq=2], reverse → [seq=2, seq=3, seq=4]
        assert session is not None
        assert len(session.messages) == 3
        assert [m["content"] for m in session.messages] == ["msg-2", "msg-3", "msg-4"]

    @patch("lib.session.pg_session_manager.transaction")
    def test_load_returns_empty_when_no_meta(self, mock_trans, mock_db_and_psycopg):
        """Нет meta-строки → сессия не существует → None (no messages load)."""
        mgr = mock_db_and_psycopg(workspace=Path("/tmp/ws"))

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.description = [("session_key",), ("created_at",), ("updated_at",), ("last_consolidated",), ("metadata",)]
        mock_cur.fetchone.return_value = None  # no meta
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_trans.return_value.__enter__.return_value = mock_conn

        assert mgr._load("nonexistent") is None

    @patch("lib.session.pg_session_manager.transaction")
    def test_max_session_messages_min_1(self, mock_trans, mock_db_and_psycopg):
        """Защита от 0/отрицательных значений."""
        mgr = mock_db_and_psycopg(workspace=Path("/tmp/ws"), max_session_messages=0)
        assert mgr._max_session_messages == 1
        mgr = mock_db_and_psycopg(workspace=Path("/tmp/ws"), max_session_messages=-5)
        assert mgr._max_session_messages == 1
