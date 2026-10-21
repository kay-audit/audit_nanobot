"""Session isolation, bounded containers and real MessageTool media contract."""
from __future__ import annotations

import asyncio
import json
import os
import signal
import stat
import subprocess
import sys
import time
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile, ZipInfo

import pytest
from application.history import inventory
from cache.history import directory, file_lock, write
from document.history import read_lines
from document.history_archive import validate_archive
from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.message import MessageTool

from workspace.tools.legal_summarizer_history import HistoryConfig, LegalSummarizerHistoryTool

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.mark.parametrize("name", ["../bad.docx", "/bad.docx", "C:/bad.docx", "C:bad.docx", "a\\bad.docx", "safe/../../bad.txt", "x:stream"])
def test_unsafe_archive_names_even_non_docx(tmp_path, name):
    path = tmp_path / "input.zip"
    with ZipFile(path, "w") as z:
        info = ZipInfo("placeholder")
        info.filename = name
        z.writestr(info, "bad")
    with pytest.raises(ValueError):
        inventory([str(path)])


def test_symlink_and_case_duplicate_members():
    for names in (["x.docx", "X.docx"], ["link.txt"]):
        stream = BytesIO()
        with ZipFile(stream, "w") as z:
            for name in names:
                info = ZipInfo(name)
                if name == "link.txt":
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                z.writestr(info, "other")
        with ZipFile(BytesIO(stream.getvalue())) as z, pytest.raises(ValueError):
            validate_archive(z)


def test_compression_bomb_and_declared_limits():
    stream = BytesIO()
    with ZipFile(stream, "w", compression=ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", b"x" * 2000000)
    with pytest.raises(ValueError):
        read_lines(stream.getvalue())
    info = ZipInfo("x")
    info.file_size = 65 * 1024 * 1024
    info.compress_size = info.file_size
    with pytest.raises(ValueError):
        validate_archive(SimpleNamespace(infolist=lambda: [info]))
    info.file_size = info.compress_size = 1
    with pytest.raises(ValueError):
        validate_archive(SimpleNamespace(infolist=lambda: [info] * 10001))


@pytest.mark.parametrize("data", [b"broken DOCX", b"PKbroken"])
def test_corrupt_docx(data):
    with pytest.raises(BadZipFile):
        read_lines(data)


def test_dtd_rejected():
    stream = BytesIO()
    with ZipFile(stream, "w") as z:
        z.writestr("word/document.xml", '<!DOCTYPE x [<!ENTITY a "bad">]><x/>')
    with pytest.raises(ValueError):
        read_lines(stream.getvalue())


def context(session="test", sender="user"):
    return request_context(RequestContext(channel="postgres", chat_id="chat", session_key=session, sender_id=sender))


@pytest.fixture
def tool(tmp_path, monkeypatch):
    tool = LegalSummarizerHistoryTool(config=HistoryConfig(workspace_root=str(tmp_path)))
    def invoke(*args):
        pytest.fail("untrusted request reached subprocess")
    monkeypatch.setattr(tool, "_invoke", invoke)
    return tool


def execute(tool, **kwargs):
    return json.loads(asyncio.run(tool.execute(**kwargs)))


def test_missing_identity_and_chat_is_not_session(tool):
    assert execute(tool, inputs=["anything"])["status"] == "error"
    with request_context(RequestContext(channel="postgres", chat_id="test", sender_id="user")):
        assert execute(tool, inputs=["anything"])["status"] == "error"
    with request_context(RequestContext(channel="postgres", chat_id="test", session_key="test")):
        assert execute(tool, inputs=["anything"])["status"] == "error"


def test_foreign_session_input(tool, tmp_path):
    foreign = tmp_path / "workspace/data_store/cache/sessions/other/attachments"
    foreign.mkdir(parents=True)
    with context():
        assert execute(tool, inputs=[str(foreign)])["error_type"] == "session_access_denied"


def test_owner_collision_is_denied(tool, monkeypatch):
    monkeypatch.setattr(tool, "_invoke", lambda *args: {"status": "needs_input"})
    with context("a:b", "first"):
        execute(tool, action="status", operation_id="history_" + "a" * 32)
    with context("a?b", "second"):
        assert execute(tool, action="status", operation_id="history_" + "a" * 32)["error_type"] == "session_access_denied"


def test_bad_operation_and_debug_args(tool):
    with context():
        assert execute(tool, action="status", operation_id="../other")["status"] == "error"
        assert execute(tool, inputs=[], offset=1)["status"] == "error"


@pytest.mark.parametrize("status", ["completed", "partial"])
def test_real_message_tool_delivers_both_files(tmp_path, monkeypatch, status):
    from workspace.utils.session_file_store import SessionFileStore
    store = SessionFileStore(tmp_path / "workspace/data_store")
    media = ["data_store/" + store.save("test", text, name, ext=ext)["path"]
             for text, name, ext in [("# Полный отчёт", "history_report", ".md"), ('{"changes":[]}', "history_result", ".json")]]
    sent = []
    async def capture(message):
        sent.append(message)
    message = MessageTool(send_callback=capture, workspace=tmp_path / "workspace", restrict_to_workspace=True)
    tool = LegalSummarizerHistoryTool(config=HistoryConfig(workspace_root=str(tmp_path)), message_tool=message)
    monkeypatch.setattr(tool, "_invoke", lambda *a: {"status": status, "media": media})
    with context():
        result = execute(tool, action="status", operation_id="history_" + "a" * 32)
    assert result["delivery"]["status"] == "sent"
    assert len(sent) == 1 and sent[0].channel == "postgres" and sent[0].chat_id == "chat"
    assert sent[0].media == [str((tmp_path / "workspace" / p).resolve()) for p in media]
    assert all(Path(p).is_file() for p in sent[0].media)
    assert "Полный отчёт" not in json.dumps(result, ensure_ascii=False)


def test_foreign_result_is_not_delivered(tool, tmp_path, monkeypatch):
    path = tmp_path / "workspace/data_store/cache/sessions/other/results/x.md"
    path.parent.mkdir(parents=True)
    path.write_text("secret")
    (path.parents[2] / "test/results").mkdir(parents=True)
    monkeypatch.setattr(tool, "_invoke", lambda *a: {"status": "completed", "media": [str(path), str(path)]})
    with context():
        result = execute(tool, action="status", operation_id="history_" + "a" * 32)
    assert result["error_type"] == "session_access_denied"


def test_subprocess_crash_releases_os_lock(tmp_path):
    lock = tmp_path / "run.lock"
    code = "import sys,time,os; from pathlib import Path; sys.path.insert(0,sys.argv[1]); from cache.history import file_lock;\nwith file_lock(Path(sys.argv[2])):\n print(os.getpid(),flush=True); time.sleep(30)"
    child = subprocess.Popen([sys.executable, "-c", code, str(SCRIPTS), str(lock)], stdout=subprocess.PIPE, text=True)
    try:
        actual_pid = int(child.stdout.readline().strip())
        with file_lock(lock, blocking=False) as acquired:
            assert not acquired
        os.kill(actual_pid, signal.SIGTERM)
        child.wait(timeout=5)
        with file_lock(lock, blocking=False) as acquired:
            assert acquired
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
        child.stdout.close()


def test_interrupted_state_and_session_lookup(tmp_path):
    from application.history_pipeline import status
    op = "history_" + "a" * 32
    path = directory(tmp_path, "test", op)
    write(path / "state.json", {"status": "running", "updated_at": time.time() - 120})
    assert status(op, tmp_path, "test")["status"] == "interrupted"
    assert status(op, tmp_path, "other")["status"] == "error"


@pytest.mark.parametrize("bad", [{"max_output_tokens": True}, {"timeout_sec": "120"}, {"temperature": float('nan')}, {"event_max_chars": 1000001}])
def test_policy_rejects_invalid_types(bad):
    from pydantic import ValidationError

    from lib.core.project_settings import SkillHistorySettings
    with pytest.raises(ValidationError):
        SkillHistorySettings.model_validate(bad)


def test_native_background_to_message_end_to_end(tmp_path, monkeypatch):
    import shutil

    from test_doc_history import REVIEW, TARGET, docx, sample
    repo = Path(__file__).resolve().parents[4]
    skill = tmp_path / "workspace/skills/legal_summarizer"
    shutil.copytree(repo / "workspace/skills/legal_summarizer/scripts", skill / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(repo / "workspace/skills/legal_summarizer/prompts", skill / "prompts")
    inputs = tmp_path / "workspace/data_store/cache/sessions/test/attachments"
    inputs.mkdir(parents=True)
    (inputs / TARGET).write_bytes(docx([TARGET[:-5]]))
    (inputs / REVIEW).write_bytes(docx(sample()))
    site = tmp_path / "site"
    site.mkdir()
    (site / "sitecustomize.py").write_text('''import sys,types,json
config=types.ModuleType("config")
config.ConfigurationError=RuntimeError
config.SETTINGS={"skills":{"legal_summarizer":{}},"agents":{"defaults":{"provider":"local","model":"test","contextWindowTokens":40768}},"providers":{"local":{"apiBase":"http://unused/v1"}}}
sys.modules["config"]=config
llm=types.ModuleType("lib.services.llm_client")
def call(messages,**kwargs):
 return json.dumps({"results":[{"id":e["id"],"meaning":"Mock explanation"} for e in json.loads(messages[1]["content"])["events"]]})
llm.call_llm=call
sys.modules["lib.services.llm_client"]=llm
''', encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(site), str(repo)]))
    sent = []
    async def capture(message):
        sent.append(message)
    tool = LegalSummarizerHistoryTool(config=HistoryConfig(workspace_root=str(tmp_path)),
                                     message_tool=MessageTool(send_callback=capture, workspace=tmp_path / "workspace"))
    with context():
        result = execute(tool, inputs=[str(inputs)], target=TARGET)
        assert result["status"] == "running", result
        op = result["operation_id"]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            result = execute(tool, action="status", operation_id=op)
            if result["status"] in ("completed", "partial", "error"):
                break
            time.sleep(.1)
        assert result["status"] == "completed", result
        assert result["delivery"]["status"] == "sent"
        assert len(sent) == 1 and len(sent[0].media) == 2
        full = json.loads(Path(sent[0].media[1]).read_text(encoding="utf-8"))
        assert len(full["changes"]) == 2
        assert full["changes"][1]["before"] == "Срок 10 дней"
        assert full["changes"][1]["after"] == "Срок 20 дней"
        assert "changes" not in result
        with context("other", "foreign"):
            foreign = execute(tool, action="status", operation_id=op)
            assert foreign["status"] == "error"
        assert len(sent) == 1


def test_delivery_failure_is_explicit(tmp_path, monkeypatch):
    from nanobot.agent.tools.base import ToolResult
    base = tmp_path / "workspace/data_store/cache/sessions/test/results"
    base.mkdir(parents=True)
    files = [base / "history_report.md", base / "history_result.json"]
    for file in files:
        file.write_text("test", encoding="utf-8")
    async def fail(**kwargs):
        return ToolResult.error("Error: channel unavailable")
    tool = LegalSummarizerHistoryTool(config=HistoryConfig(workspace_root=str(tmp_path)),
                                     message_tool=SimpleNamespace(execute=fail))
    monkeypatch.setattr(tool, "_invoke", lambda *a: {"status": "completed", "media": [str(p) for p in files]})
    with context():
        assert execute(tool, action="status", operation_id="history_" + "a" * 32)["delivery"]["status"] == "error"


def test_checkpoint_retries_transient_windows_replace(tmp_path, monkeypatch):
    import cache.history as module
    actual = module._atomic_write_json
    attempts = []
    def locked_reader(path, payload):
        attempts.append(path)
        if len(attempts) < 3:
            raise PermissionError("reader temporarily holds the target")
        return actual(path, payload)
    monkeypatch.setattr(module, "_atomic_write_json", locked_reader)
    write(tmp_path / "state.json", {"status": "completed"})
    assert len(attempts) == 3
    assert json.loads((tmp_path / "state.json").read_text())["status"] == "completed"


def test_real_uploaded_names_preserve_automatic_overview_match(tmp_path):
    from application.history import analyze
    from test_doc_history import REVIEW, TARGET, docx, sample

    from workspace.utils.session_file_store import SessionFileStore
    original = tmp_path / "original"
    original.mkdir()
    (original / TARGET).write_bytes(docx([TARGET[:-5]]))
    (original / REVIEW).write_bytes(docx(sample()))
    store = SessionFileStore(tmp_path / "workspace/data_store")
    law = store.save_attachment("test", str(original / TARGET))
    review = store.save_attachment("test", str(original / REVIEW))
    assert law and review
    assert Path(law["path"]).name != TARGET
    inputs = tmp_path / "workspace/data_store/cache/sessions/test/attachments"
    result = analyze([str(inputs)], target=Path(law["path"]).name, _full=True)
    assert result["status"] == "ok"
    assert result["total_changes"] == 2
