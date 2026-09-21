"""Contracts for the copyable appeals-analyzer standalone CLI."""
from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "workspace" / "skills" / "appeals-analyzer"
CLI_PATH = SKILL / "scripts" / "cli.py"


def _load_cli():
    spec = importlib.util.spec_from_file_location("appeals_cli_contract", CLI_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _drop_runtime():
    for name in list(sys.modules):
        if name == "appeals_analyzer_standalone" or name.startswith("appeals_analyzer_standalone."):
            sys.modules.pop(name, None)


def test_standalone_loader_rejects_foreign_utils(monkeypatch):
    cli = _load_cli()
    _drop_runtime()
    foreign_utils = ModuleType("utils")
    foreign_utils.__path__ = ["foreign-utils"]
    monkeypatch.setitem(sys.modules, "utils", foreign_utils)
    with pytest.raises(RuntimeError, match="another top-level utils"):
        cli.load_standalone_runner()


def test_standalone_loader_uses_workspace_shared_db():
    cli = _load_cli()
    _drop_runtime()
    shared_db = cli.load_shared_db()
    runner = cli.load_standalone_runner()
    facade = sys.modules["appeals_analyzer_standalone.utils.db"]
    assert runner.__module__ == "appeals_analyzer_standalone.scripts.appeals_reports"
    assert Path(shared_db.__file__).resolve() == (ROOT / "workspace/utils/db.py").resolve()
    assert facade.run is shared_db.run


def test_standalone_logging_writes_rotating_file(tmp_path):
    cli = _load_cli()
    root = logging.getLogger()
    old_handlers, old_level = list(root.handlers), root.level
    log_file = tmp_path / "appeals.log"
    try:
        cli.configure_logging("INFO", log_file)
        logging.getLogger("contract").info("stage visible")
        for handler in root.handlers:
            handler.flush()
        assert "stage visible" in log_file.read_text(encoding="utf-8")
    finally:
        for handler in root.handlers:
            handler.close()
        root.handlers[:] = old_handlers
        root.setLevel(old_level)


def test_vllm_is_not_called_when_standalone_disables_it(monkeypatch):
    cli = _load_cli()
    _drop_runtime()
    cli.load_standalone_runner()
    local_qwen = sys.modules["appeals_analyzer_standalone.utils.local_qwen"]
    monkeypatch.setenv("APPEALS_DISABLE_VLLM", "1")
    monkeypatch.setattr(local_qwen, "_post_json", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("vLLM HTTP called")))
    assert local_qwen.call_vllm_http([{"role": "user", "content": "x"}]) == ""


def test_standalone_db_runtime_uses_central_config_and_pool(monkeypatch):
    cli = _load_cli()
    calls = []
    fake_db = SimpleNamespace(
        resolve_dsn=lambda: "postgresql://central-config",
        set_pool_config=lambda value: calls.append(("pool", value)),
        configure=lambda value: calls.append(("configure", value)),
        start=lambda: calls.append(("start",)),
    )
    config = ModuleType("config")
    config.SETTINGS = {"channels": {"postgres": {"pool": {"max_conn": 4}}}}
    monkeypatch.setitem(sys.modules, "config", config)

    assert cli.start_standalone_db_runtime(fake_db) is None
    assert calls == [
        ("pool", {"max_conn": 4}),
        ("configure", "postgresql://central-config"),
        ("start",),
    ]

def test_standalone_main_stops_runtime_it_started(monkeypatch):
    cli = _load_cli()
    shutdowns = []
    fake_db = SimpleNamespace(shutdown=lambda: shutdowns.append(True))
    args = SimpleNamespace(
        prompt='"", "", "", "query"', prompt_file=None,
        positional_prompt=[], session_id="test", log_file=None,
        console_only=True, log_level="INFO", allow_vllm=False,
    )
    monkeypatch.setattr(cli, "build_parser", lambda: SimpleNamespace(parse_args=lambda: args))
    monkeypatch.setattr(cli, "configure_logging", lambda *args: None)
    monkeypatch.setattr(cli, "load_shared_db", lambda: fake_db)
    starts = []
    monkeypatch.setattr(cli, "start_standalone_db_runtime", lambda db: starts.append(True))

    async def runner(**kwargs):
        return "ok"

    monkeypatch.setattr(cli, "load_standalone_runner", lambda: runner)
    assert cli.main() == 0
    assert starts == [True]
    assert shutdowns == [True]


def test_standalone_main_stops_runtime_when_analysis_fails(monkeypatch):
    cli = _load_cli()
    calls = []
    fake_db = SimpleNamespace(shutdown=lambda: calls.append("shutdown"))
    args = SimpleNamespace(
        prompt='"", "", "", "query"', prompt_file=None,
        positional_prompt=[], session_id="test", log_file=None,
        console_only=True, log_level="INFO", allow_vllm=False,
    )
    monkeypatch.setattr(cli, "build_parser", lambda: SimpleNamespace(parse_args=lambda: args))
    monkeypatch.setattr(cli, "configure_logging", lambda *args: None)
    monkeypatch.setattr(cli, "load_shared_db", lambda: fake_db)
    monkeypatch.setattr(
        cli,
        "start_standalone_db_runtime",
        lambda db: calls.append("start"),
    )

    async def failing_runner(**kwargs):
        calls.append("runner")
        raise RuntimeError("analysis failed")

    monkeypatch.setattr(cli, "load_standalone_runner", lambda: failing_runner)
    assert cli.main() == 1
    assert calls == ["start", "runner", "shutdown"]


def test_shell_entrypoint_keeps_imports_isolated_and_disables_vllm():
    shell = (SKILL / "appeals_analyze.sh").read_text(encoding="utf-8")
    assert "APPEALS_DISABLE_VLLM" in shell
    assert "scripts/cli.py" in shell
    assert "PYTHONPATH" not in shell
    assert "PG_DSN" not in shell
    assert "GREENPLUM_DSN" not in shell
