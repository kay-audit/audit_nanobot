"""Isolation and error-surface contracts for the native appeals tool."""
from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.asyncio
@pytest.mark.parametrize('failed_send', [False, True])
async def test_native_appeals_marks_its_real_message_publication(monkeypatch, tmp_path, failed_send):
    from contextlib import contextmanager, nullcontext
    from types import SimpleNamespace
    from nanobot.agent.tools.context import RequestContext, request_context, current_request_context
    from nanobot.agent.tools.message import MessageTool
    from workspace.tools import appeals_analyzer as native
    from lib.utils.verbatim_report import verified_source

    monkeypatch.setenv('APPEALS_ANALYZER_RUNTIME', 'production')
    monkeypatch.setenv('NANOBOT_SKILLS_RUNTIME', 'production')
    report = ('## Выводы\n1. Полные шаги проверки\n' + 'Я'*11000)[:11000]
    path = tmp_path / 'appeals.xlsx'
    path.write_bytes(b'fixture')

    @contextmanager
    def artifacts_scope(directory, paths):
        paths.append(path)
        yield

    artifacts = SimpleNamespace(artifact_scope=artifacts_scope, session_results=lambda session: tmp_path)
    backend = SimpleNamespace(backend_scope=lambda mode: nullcontext())
    real_import = native.import_module
    def load(name):
        if name == 'appeals_analyzer_runtime.utils.appeals_artifacts': return artifacts
        if name == 'appeals_analyzer_runtime.utils.data_store': return backend
        return real_import(name)
    monkeypatch.setattr(native, 'import_module', load)

    async def runner(**kwargs):
        assert kwargs['session_id'] == 'postgres:appeals'
        return report

    monkeypatch.setattr(native.AppealsAnalyzerTool, '_load_runner', staticmethod(lambda: runner))
    outbound = []
    async def send(msg):
        outbound.append(msg)
        if failed_send:
            raise RuntimeError('fixture: delivery failed')

    ctx = RequestContext(channel='postgres', chat_id='chat', message_id='m-1', session_key='postgres:appeals',
                         metadata={'origin_message_id': 'm-1', 'answer_id': 'a-1', '_verbatim_report_source': 'untrusted'})
    original = dict(ctx.metadata)
    tool = native.AppealsAnalyzerTool(config=native.AppealsAnalyzerToolConfig(),
                                     message_tool=MessageTool(send_callback=send, workspace=tmp_path))
    with request_context(ctx):
        result = await tool.execute(prompt='Обращения')
        assert current_request_context() is ctx
        assert ctx.metadata == original
    assert len(outbound) == 1 and outbound[0].content == report
    assert outbound[0].media == [str(path)]
    assert verified_source(outbound[0], outbound[0].metadata) == 'appeals_analyzer'
    if failed_send:
        assert result.is_error
    else:
        assert result == report


def _drop_appeals_runtime_modules() -> None:
    for name in list(sys.modules):
        if name == "appeals_analyzer_runtime" or name.startswith("appeals_analyzer_runtime."):
            sys.modules.pop(name, None)


def test_native_loader_ignores_foreign_utils_without_loading_gp(monkeypatch):
    from workspace.tools.appeals_analyzer import AppealsAnalyzerTool

    _drop_appeals_runtime_modules()
    foreign_utils = ModuleType("utils")
    foreign_utils.__path__ = ["foreign-utils-path"]
    monkeypatch.setitem(sys.modules, "utils", foreign_utils)
    runner = AppealsAnalyzerTool._load_runner()
    assert runner.__module__ == "appeals_analyzer_runtime.scripts.appeals_reports"
    assert "appeals_analyzer_runtime.utils.db" not in sys.modules
    assert "appeals_analyzer_runtime.utils.bge_search_engine" not in sys.modules
    assert sys.modules["utils"] is foreign_utils


def test_skill_directory_resolution_is_deterministic(tmp_path):
    from workspace.tools.appeals_analyzer import AppealsAnalyzerTool

    skills = tmp_path / "skills"
    hyphen = skills / "appeals-analyzer"
    hyphen.mkdir(parents=True)
    assert AppealsAnalyzerTool._resolve_skill_dir(tmp_path) == hyphen
    underscore = skills / "appeals_analyzer"
    underscore.mkdir()
    with pytest.raises(RuntimeError, match="Ambiguous"):
        AppealsAnalyzerTool._resolve_skill_dir(tmp_path)


@pytest.mark.asyncio
async def test_native_error_does_not_expose_traceback(monkeypatch):
    from workspace.tools.appeals_analyzer import AppealsAnalyzerTool, AppealsAnalyzerToolConfig

    # Тест проверяет ИМЕННО production-ветку execute(): подложенная ошибка в
    # _load_runner должна быть перехвачена и отдана без traceback. На стенде
    # appeals-analyzer живёт в testing-рантайме (APPEALS_ANALYZER_RUNTIME=
    # testing из feature.yaml), и execute() уходит в load_testing_module() —
    # _load_runner там не вызывается, и ошибка просто не возникает. Рантайм
    # фиксируем явно, чтобы тест не зависел от окружения машины.
    monkeypatch.setenv("APPEALS_ANALYZER_RUNTIME", "production")
    monkeypatch.setenv("NANOBOT_SKILLS_RUNTIME", "production")

    def fail():
        raise RuntimeError("SELECT secret FROM internal_table at C:/private/runtime.py")

    monkeypatch.setattr(AppealsAnalyzerTool, "_load_runner", staticmethod(fail))
    result = await AppealsAnalyzerTool(config=AppealsAnalyzerToolConfig()).execute(prompt="x")
    rendered = str(result)
    assert "Traceback" not in rendered
    assert "internal_table" not in rendered
    assert "private/runtime.py" not in rendered
    assert "RuntimeError" in rendered
