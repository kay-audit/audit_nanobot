"""One logical history run; detached worker, resumable semantic batches, artifacts."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

from cache.history import directory, file_lock, fingerprint, llm_lock, read, write
from llm.history import HistoryRequests, explain_batch, runtime_settings
from output.history import compact_result, persist

from application.history import analyze


def session_key(inputs, explicit=None):
    from workspace.utils.session_key import safe_session_key
    if explicit:
        return safe_session_key(explicit)
    raise ValueError("Требуется явная доверенная session_key; путь не является источником идентичности")


def small_input_result(result):
    result = dict(result)
    if "attachments" in result:
        result["attachment_count"] = len(result["attachments"])
        result["attachments"] = result["attachments"][:20]
    return result


def prepare(inputs, target, overview, root, session, *, settings=None):
    parsed = analyze(inputs, target=target, overview=overview, _full=True)
    if parsed["status"] == "needs_input":
        return None, small_input_result(parsed)
    cfg, context, policy = settings or runtime_settings()
    requests = HistoryRequests(context, policy)
    operation_id = fingerprint(parsed, cfg, context, asdict(policy), requests.prompt)
    path = directory(root, session, operation_id)
    with file_lock(path / "prepare.lock"):
        if read(path / "parsed.json") is None:
            write(path / "parsed.json", parsed)
        if read(path / "state.json") is None:
            write(path / "state.json", {"status": "running", "operation_id": operation_id,
                                        "total_changes": parsed["total_changes"], "updated_at": time.time()})
    return operation_id, None


def artifacts_exist(payload, root):
    return all((root / "workspace" / payload.get(k, "__missing__")).is_file()
               for k in ("result_path", "result_json_path"))


def status(operation_id, root, session):
    path = directory(root, session, operation_id)
    state = read(path / "state.json")
    if state is None:
        return {"status": "error", "message": "Операция не найдена в этой сессии"}
    if state["status"] in ("completed", "partial"):
        if artifacts_exist(state, root):
            return state
        state = {"status": "interrupted", "operation_id": operation_id,
                 "message": "Файлы результата отсутствуют; повторите запуск с той же парой"}
    elif state["status"] not in ("error", "interrupted") and time.time() - state.get("updated_at", 0) > 60:
        with file_lock(path / "run.lock", blocking=False) as available:
            if available:
                state = {**state, "status": "interrupted", "message": "Процесс завершился до отчёта. Повторите запуск: готовые пояснения будут переиспользованы"}
    return {"mode": "doc_history", "poll_after_seconds": 90, **state}


def run_operation(operation_id, root, session, *, settings=None, call=None):
    path = directory(root, session, operation_id)
    with file_lock(path / "run.lock", blocking=False) as acquired:
        if not acquired:
            return {"status": "running", "mode": "doc_history", "operation_id": operation_id, "poll_after_seconds": 90}
        previous = read(path / "state.json") or {}
        if previous.get("status") == "completed" and artifacts_exist(previous, root):
            return previous
        parsed = read(path / "parsed.json")
        if parsed is None:
            raise ValueError("Missing prepared history")
        cfg, context, policy = settings or runtime_settings()
        requests = HistoryRequests(context, policy)
        if fingerprint(parsed, cfg, context, asdict(policy), requests.prompt) != operation_id:
            raise ValueError("Настройки изменились. Запустите новую операцию с исходной парой")
        saved = read(path / "explanations.json") or {}
        ids = {e["id"] for e in parsed["changes"]}
        if len(ids) != len(parsed["changes"]):
            raise ValueError("Duplicate parser ids")
        saved = {k: v for k, v in saved.items() if k in ids and v.get("explanation_status") == "completed"}
        running = {"status": "running", "mode": "doc_history", "operation_id": operation_id,
                   "total_changes": len(ids), "explained_changes": len(saved)}

        def checkpoint(rows):
            saved.update(rows)
            write(path / "explanations.json", saved)
            write(path / "state.json", {**running, "explained_changes": sum(v["explanation_status"] == "completed" for v in saved.values()), "updated_at": time.time()})

        checkpoint({})
        pending = [e for e in parsed["changes"] if e["id"] not in saved]
        try:
            for n, batch in enumerate(requests.batches(pending), 1):
                print(f"history: batch {n}, events={len(batch)}", file=sys.stderr, flush=True)
                # Thread lock is shared with summary wrappers; this OS lock also
                # prevents concurrent history subprocesses overloading one vLLM.
                with llm_lock(root):
                    kwargs = {"call": call} if call else {}
                    explain_batch(batch, requests, cfg, checkpoint, **kwargs)
        except Exception as exc:
            # Preserve every fact even if planning, transport or a batch fails.
            for event in pending:
                if event["id"] not in saved:
                    saved[event["id"]] = {"meaning": "Автоматическое пояснение не получено; исходные факты сохранены.",
                                          "needs_review": True, "reason": type(exc).__name__, "explanation_status": "failed"}
            checkpoint({})
        result = {**parsed, "operation_id": operation_id,
                  "changes": [{**e, **saved[e["id"]]} for e in parsed["changes"]]}
        result["explained_changes"] = sum(e["explanation_status"] == "completed" for e in result["changes"])
        result["needs_review"] = sum(e["needs_review"] for e in result["changes"])
        result["status"] = "partial" if result["needs_review"] or not ids else "completed"
        with file_lock(path.parent / "artifacts.lock"):
            artifacts = persist(result, root, session)
        compact = compact_result(result, artifacts)
        write(path / "state.json", compact)
        return compact


def start(inputs, *, target=None, overview=None, root=None, session=None, background=False, settings=None, call=None, profile="test"):
    root = Path(root or Path(__file__).resolve().parents[5]).resolve()
    if not session:
        parsed = analyze(inputs, target=target, overview=overview, _full=True)
        if parsed["status"] == "needs_input":
            return small_input_result(parsed)
    session = session_key(inputs, session)
    operation_id, clarification = prepare(inputs, target, overview, root, session, settings=settings)
    if clarification:
        return clarification
    current = status(operation_id, root, session)
    if current["status"] == "completed":
        return current
    if not background:
        return run_operation(operation_id, root, session, settings=settings, call=call)
    return launch(operation_id, root, session, profile=profile)


def launch(operation_id, root, session, *, profile="test"):
    path = directory(root, session, operation_id)
    current = status(operation_id, root, session)
    if current["status"] in ("completed", "error"):
        return current
    with file_lock(path / "launch.lock"):
        # Multiple starts are harmless: the worker owns run.lock for its lifetime.
        with file_lock(path / "run.lock", blocking=False) as idle:
            if not idle:
                return status(operation_id, root, session)
        cli = Path(__file__).resolve().parents[1] / "cli_history.py"
        argv = [sys.executable, str(cli), "--run-operation", operation_id,
                "--workspace-root", str(root), "--session-key", session, "--profile", profile]
        with (path / "worker.log").open("ab") as log:
            options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS} if os.name == "nt" else {"start_new_session": True}
            subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log, cwd=root,
                             env={**os.environ, "PYTHONIOENCODING": "utf-8"}, **options)
    return {"status": "running", "mode": "doc_history", "operation_id": operation_id,
            "total_changes": current.get("total_changes", 0), "poll_after_seconds": 90,
            "message": "Анализ идёт внутри навыка. Проверяйте только состояние операции; не читайте исходные изменения."}
