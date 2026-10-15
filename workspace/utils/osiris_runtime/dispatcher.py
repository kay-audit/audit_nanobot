"""Explicit operation handlers and a reusable worker loop without ML or SDK imports."""
from __future__ import annotations

import json
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import client
from .errors import OsirisPayloadError, OsirisUnknownOperationError
from .protocol import check_artifacts, confined, json_bytes, read_json, request_paths
from .worker import (
    WorkerActivity,
    claim_new_requests,
    recover_processing_after_restart,
    should_idle_stop,
)


@dataclass(frozen=True)
class WorkerRequest:
    session_id: str
    consumer_id: str
    request_id: str
    query: str
    artifacts: dict[str, Path]
    output_dir: Path
    expires_at: float
    service_name: str = ""
    operation: str = ""
    generation: str | None = None


@dataclass(frozen=True)
class WorkerResult:
    value: object
    artifacts: dict[str, Path] = field(default_factory=dict)


class HandlerRegistry:
    def __init__(self):
        self._handlers: dict[str, tuple[Callable, Callable | None]] = {}

    def register(self, operation: str, handler: Callable, validator: Callable | None = None):
        from .protocol import identity
        identity(operation, "operation")
        if operation in self._handlers or not callable(handler):
            raise ValueError("Duplicate or invalid worker handler")
        self._handlers[operation] = (handler, validator)
        return self

    @property
    def capabilities(self):
        return tuple(self._handlers)

    def invoke(self, operation, payload, context: WorkerRequest):
        try:
            handler, validator = self._handlers[operation]
        except KeyError as exc:
            raise OsirisUnknownOperationError("Worker handler is not registered") from exc
        if validator is not None:
            validator(payload)
        return handler(payload, context)


def process_json_request(profile, manifest_path: Path, handlers: HandlerRegistry):
    manifest = read_json(manifest_path, profile.max_payload_bytes + 32768)
    dirs, paths = request_paths(profile, manifest)
    expires = float(manifest["expires_at"])
    correlation = {key: manifest[key] for key in (
        "request_id", "request_type", "operation", "service_name", "session_id",
        "consumer_id", "protocol_version",
    )}
    if profile.deployment is not None:
        from .deployment import correlation as shared_correlation
        correlation = shared_correlation(manifest)
    published = False
    try:
        if time.time() >= expires:
            return
        body = read_json(confined(paths["input_path"], profile.nfs_root, must_exist=True), profile.max_payload_bytes)
        if not isinstance(body, dict) or "payload" not in body:
            raise OsirisPayloadError("Request body must contain a payload")
        artifacts = check_artifacts(body.get("artifacts", []), dirs["input"] / f"{manifest['request_id']}.files", profile.max_artifact_bytes)
        output_dir = confined(dirs["output"] / f"{manifest['request_id']}.files", profile.nfs_root)
        context = WorkerRequest(manifest["session_id"], manifest["consumer_id"], manifest["request_id"],
                                manifest["query"], artifacts, output_dir, expires,
                                profile.service_name, manifest["operation"], manifest.get("generation"))
        raw = handlers.invoke(manifest["operation"], body["payload"], context)
        result = raw if isinstance(raw, WorkerResult) else WorkerResult(raw)
        records = []
        for name, path in result.artifacts.items():
            from .protocol import identity
            identity(name, "artifact name")
            path = confined(path, output_dir, must_exist=True)
            records.append(dict(name=name, path=path.relative_to(output_dir).as_posix(), size=path.stat().st_size))
        check_artifacts(records, output_dir, profile.max_artifact_bytes)
        response = dict(correlation, result=result.value, artifacts=records)
        json_bytes(response, profile.max_result_bytes)
        if time.time() < expires:
            client._atomic_json(response, paths["output_path"])
            published = True
    except Exception as exc:
        code = "unknown_operation" if isinstance(exc, OsirisUnknownOperationError) else "invalid_payload" if isinstance(exc, OsirisPayloadError) else "worker_error"
        if time.time() < expires:
            client._atomic_json(dict(correlation, error_code=code), paths["error_path"])
            published = True
    finally:
        paths["input_path"].unlink(missing_ok=True)
        manifest_path.unlink(missing_ok=True)
        if not published:
            from .json_transport import cleanup
            cleanup(profile, manifest["session_id"], manifest["request_id"])


def run_worker(profile, handlers: HandlerRegistry, *, poll_sec=.25):
    """Handlers are constructed by trusted worker code before declaring READY."""
    if set(profile.capabilities) != set(handlers.capabilities):
        raise ValueError("Profile and worker capabilities must match")
    profile.prepare_nfs()
    try:
        metadata = json.loads(profile.metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    if "idle_timeout_sec" in metadata:
        profile = replace(profile, idle_timeout_sec=float(metadata["idle_timeout_sec"]))
    generation = metadata.get("generation")
    activity = WorkerActivity(profile)
    tasks = queue.Queue()
    stop = threading.Event()
    def heartbeat():
        while not stop.is_set():
            client._atomic_json(activity.snapshot(tasks.qsize(), generation), profile.heartbeat_path)
            stop.wait(1)
    recover_processing_after_restart(profile)
    activity.ready()
    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        while True:
            claim_new_requests(profile, tasks)
            try:
                path = tasks.get(timeout=poll_sec)
            except queue.Empty:
                if should_idle_stop(profile, activity, tasks):
                    break
                continue
            activity.begin()
            try:
                process_json_request(profile, path, handlers)
            except (OSError, ValueError, KeyError, TypeError):
                if path.exists():
                    path.replace(profile.nfs_root / "failed" / path.name)
            finally:
                activity.end()
                tasks.task_done()
    finally:
        stop.set()
        thread.join(timeout=2)
        activity.stopped()
        client._atomic_json(activity.snapshot(tasks.qsize(), generation), profile.heartbeat_path)
