"""Contract tests for the native ``ior_analyzer`` project tool."""
from __future__ import annotations

import sys
import importlib.util
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize('failed_send', [False, True])
async def test_native_ior_marks_only_its_real_message_publication(monkeypatch, tmp_path, failed_send):
    from nanobot.agent.tools.context import RequestContext, request_context, current_request_context
    from nanobot.agent.tools.message import MessageTool
    from workspace.tools.ior_analyzer import IORAnalyzerTool, IORAnalyzerToolConfig
    from lib.utils.verbatim_report import verified_source
    IORAnalyzerTool._prepare_skill_utils_namespace(Path(__file__).resolve().parents[1] / 'workspace/skills/ior-analyzer')
    from utils import ior_artifacts as artifacts
    original_scope = artifacts.artifact_scope
    monkeypatch.setattr(artifacts, 'artifact_scope', lambda directory, paths: original_scope(tmp_path, paths))
    report = ('## Гипотезы\n1. Полные шаги проверки\n' + 'Я'*11000)[:11000]

    async def runner(**kwargs):
        assert kwargs['trusted_preset'] is False
        path = tmp_path / 'report.xlsx'
        path.write_bytes(b'fixture')
        artifacts.register_artifact(path)
        return report

    outbound = []
    async def send(msg):
        outbound.append(msg)
        if failed_send:
            raise RuntimeError('fixture: send failed')

    monkeypatch.setattr(IORAnalyzerTool, '_load_runner', staticmethod(lambda: runner))
    context = RequestContext(channel='postgres', chat_id='chat', message_id='m-1', session_key='postgres:chat',
                             metadata={'origin_message_id': 'm-1', 'answer_id': 'a-1', '_verbatim_report_source': 'untrusted'})
    original = dict(context.metadata)
    tool = IORAnalyzerTool(config=IORAnalyzerToolConfig(), message_tool=MessageTool(send_callback=send, workspace=tmp_path))
    with request_context(context):
        result = await tool.execute(prompt='ИОР')
        assert current_request_context() is context
        assert context.metadata == original
    assert len(outbound) == 1 and outbound[0].content == report
    assert verified_source(outbound[0], outbound[0].metadata) == 'ior_analyzer'
    assert outbound[0].media == [str(tmp_path / 'report.xlsx')]
    if failed_send:
        assert result.is_error
    else:
        assert result == report


def _load_data_store_module():
    path = (
        Path(__file__).resolve().parent.parent
        / "workspace"
        / "skills"
        / "ior-analyzer"
        / "utils"
        / "data_store.py"
    )
    spec = importlib.util.spec_from_file_location("ior_data_store_contract", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ctx(section=None):
    gateway = SimpleNamespace()
    if section is not None:
        gateway.ior_analyzer = section
    return SimpleNamespace(_settings_ref=SimpleNamespace(gateway=gateway))


def test_tool_metadata_and_schema():
    from workspace.tools.ior_analyzer import IORAnalyzerTool, IORAnalyzerToolConfig

    tool = IORAnalyzerTool(config=IORAnalyzerToolConfig())
    assert tool.name == "ior_analyzer"
    assert "ИОР" in tool.description
    assert tool.parameters["required"] == ["prompt"]
    assert "preset" in tool.parameters["properties"]
    assert "session_id" not in tool.parameters["properties"]
    preset_description = tool.parameters["properties"]["preset"]["description"]
    assert "EVE-ID" in preset_description
    assert "DRP" in preset_description
    assert "SBR-фильтрация не поддерживается" in preset_description


def test_enabled_and_create_read_gateway_configuration():
    from workspace.tools.ior_analyzer import IORAnalyzerTool

    assert IORAnalyzerTool.enabled(_ctx({"enable": False})) is False
    tool = IORAnalyzerTool.create(_ctx({"enable": True}))
    assert tool.config.enable is True


def test_skill_utils_path_extends_gateway_utils_without_shadowing_db(monkeypatch, tmp_path):
    """IOR modules become visible while gateway ``utils.db`` keeps priority."""
    from workspace.tools.ior_analyzer import IORAnalyzerTool

    skill_dir = tmp_path / "ior-analyzer"
    skill_utils = skill_dir / "utils"
    skill_utils.mkdir(parents=True)
    gateway_utils = tmp_path / "gateway-utils"
    gateway_utils.mkdir()
    loaded_utils = ModuleType("utils")
    loaded_utils.__path__ = [str(gateway_utils)]
    monkeypatch.setitem(sys.modules, "utils", loaded_utils)

    IORAnalyzerTool._prepare_skill_utils_namespace(skill_dir)

    assert loaded_utils.__path__ == [str(gateway_utils), str(skill_utils)]


@pytest.mark.asyncio
async def test_execute_delegates_to_skill_runner(monkeypatch):
    from workspace.tools.ior_analyzer import IORAnalyzerTool, IORAnalyzerToolConfig

    captured = {}

    async def fake_runner(**kwargs):
        captured.update(kwargs)
        return "готовый отчёт"

    monkeypatch.setattr(IORAnalyzerTool, "_load_runner", staticmethod(lambda: fake_runner))
    result = await IORAnalyzerTool(config=IORAnalyzerToolConfig()).execute(
        prompt="Покажи потери", preset="financial_consequences_ior", session_id="pg:42"
    )

    assert result == "готовый отчёт"
    assert captured == {
        "trusted_preset": False,
        "preset_name": "financial_consequences_ior",
        "session_id": "pg:42",
        "user_prompt": "Покажи потери",
    }


@pytest.mark.asyncio
async def test_execute_delivers_exact_artifacts_to_request_session(monkeypatch, tmp_path):
    from workspace.tools.ior_analyzer import IORAnalyzerTool, IORAnalyzerToolConfig
    from nanobot.agent.tools.base import ToolResult
    from nanobot.agent.tools.context import RequestContext, request_context
    IORAnalyzerTool._prepare_skill_utils_namespace(Path(__file__).resolve().parent.parent / "workspace" / "skills" / "ior-analyzer")
    from utils.ior_artifacts import output_directory, register_artifact
    import utils.ior_artifacts as ior_artifacts
    scope_directories = []
    real_scope = ior_artifacts.artifact_scope
    def test_scope(directory, paths):
        scope_directories.append(directory)
        return real_scope(tmp_path, paths)
    monkeypatch.setattr(ior_artifacts, "artifact_scope", test_scope)
    monkeypatch.setitem(sys.modules, "lib.services.skill_runtime_mode", None)

    deliveries = []
    class MessageTool:
        async def execute(self, **kwargs):
            deliveries.append(kwargs)
            return ToolResult("sent")

    async def fake_runner(**kwargs):
        assert kwargs["session_id"] == "postgres:current"
        directory = output_directory(Path("missing"))
        directory.mkdir(parents=True, exist_ok=True)
        for name in ("ior-test-artifact.xlsx", "ior-test-chart.png"):
            path = directory / name
            path.write_bytes(b"artifact")
            register_artifact(path)
        foreign = tmp_path.parent / f"{tmp_path.name}-foreign.png"
        foreign.write_bytes(b"foreign")
        register_artifact(foreign)
        return "Отчёт без ссылки"

    monkeypatch.setattr(IORAnalyzerTool, "_load_runner", staticmethod(lambda: fake_runner))
    tool = IORAnalyzerTool(config=IORAnalyzerToolConfig(), message_tool=MessageTool())
    context = RequestContext(channel="postgres", chat_id="chat-1", session_key="postgres:current")
    with request_context(context):
        assert await tool.execute(prompt="ИОР", session_id="other-session") == "Отчёт без ссылки"
    assert scope_directories[0].name == "results"
    assert scope_directories[0].parent.name == "postgres_current"
    assert deliveries == [{
        "content": "Отчёт без ссылки",
        "media": [str(tmp_path / "ior-test-artifact.xlsx"), str(tmp_path / "ior-test-chart.png")],
    }]


def test_data_backend_factory_is_explicit(monkeypatch):
    module = _load_data_store_module()

    monkeypatch.setenv("IOR_DATA_BACKEND", "greenplum")
    monkeypatch.setattr(module, "GreenplumStore", type("FakeGP", (), {}))
    module.reset_data_store()
    assert isinstance(module.get_data_store(), module.GreenplumStore)

    monkeypatch.setenv("IOR_DATA_BACKEND", "local_duckdb")
    module.reset_data_store()
    assert isinstance(module.get_data_store(), module.DuckDBStore)


def test_greenplum_store_uses_shared_parameterized_db_layer():
    module = _load_data_store_module()
    captured = {}

    class Cursor:
        description = [("incdnt_id",), ("incdnt_sid",)]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, sql, params):
            captured["sql"] = sql
            captured["params"] = params

        def fetchall(self):
            return [(1, "EVE-1"), (2, "EVE-2")]

    class Connection:
        def cursor(self):
            return Cursor()

    class SharedDB:
        @staticmethod
        def resolve_dsn():
            return "configured-without-exposing-secret"

        @staticmethod
        def run(fn):
            return fn(Connection())

    store = module.GreenplumStore(db_module=SharedDB())
    frame = store.query_sql(
        "SELECT incdnt_id, incdnt_sid FROM example WHERE incdnt_id = %s",
        params=(1,),
    )

    assert list(frame.columns) == ["incdnt_id", "incdnt_sid"]
    assert frame.to_dict("records")[0] == {"incdnt_id": 1, "incdnt_sid": "EVE-1"}
    assert captured["params"] == (1,)


def test_greenplum_store_does_not_fallback_to_another_database():
    module = _load_data_store_module()

    class FailingDB:
        @staticmethod
        def resolve_dsn():
            return "configured"

        @staticmethod
        def run(_fn):
            raise ConnectionError("GP unavailable")

    store = module.GreenplumStore(db_module=FailingDB())
    with pytest.raises(ConnectionError, match="GP unavailable"):
        store.query_sql("SELECT 1")
