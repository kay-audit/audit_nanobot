"""Universal transport alongside the trusted legacy pickle client."""
from __future__ import annotations

import math
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import client
from .errors import OsirisPayloadError, OsirisRequestError, OsirisRequestTimeoutError
from .lifecycle import ensure_ready, lifecycle_lock
from .protocol import (
    check_artifacts,
    confined,
    identity,
    json_bytes,
    operation,
    read_json,
    request_identity,
)


@dataclass(frozen=True)
class InvocationResult:
    request_id: str
    value: object
    artifacts: dict[str, Path]


def submit(profile, session_id, op, payload, *, consumer_id="python", query="",
           timeout_sec=1800, artifacts=None, file_root=None, request_id=None, validate_only=False):
    operation(profile, op)
    identity(consumer_id, "consumer ID")
    if not isinstance(session_id, str) or not session_id or len(session_id) > 4096:
        raise OsirisPayloadError("Invalid session ID")
    if not isinstance(query, str) or len(query) > profile.max_payload_bytes:
        raise OsirisPayloadError("Invalid query")
    if not isinstance(timeout_sec, (float, int)) or not math.isfinite(timeout_sec) or timeout_sec <= 0:
        raise OsirisPayloadError("Invalid request timeout")
    json_bytes(payload, profile.max_payload_bytes)
    if artifacts is not None and (not isinstance(artifacts, dict) or len(artifacts) > 128):
        raise OsirisPayloadError("Invalid artifacts mapping")
    rid = request_id or uuid.uuid4().hex
    request_identity(rid)
    sources = {}
    total = 0
    for name, source in (artifacts or {}).items():
        identity(name, "artifact name")
        if file_root is None:
            raise OsirisPayloadError("Artifact source root is required")
        source = confined(source, file_root, must_exist=True)
        total += source.stat().st_size
        if total > profile.max_artifact_bytes:
            raise OsirisPayloadError("Artifact size limit exceeded")
        sources[name] = source
    if validate_only:
        return None
    profile.prepare_nfs()
    _, dirs = client.session_dirs(profile, session_id)
    staging = dirs["input"] / f"{rid}.files"
    now = time.time()
    manifest = dict(protocol_version=profile.protocol_version, service_name=profile.service_name,
                    request_id=rid, request_type=op, operation=op, serialization="json",
                    consumer_id=consumer_id, session_id=session_id, query=query,
                    created_at=now, expires_at=now + timeout_sec,
                    input_path=str(dirs["input"] / f"{rid}.json"),
                    output_path=str(dirs["output"] / f"{rid}.json"),
                    error_path=str(dirs["error"] / f"{rid}.json"))
    json_bytes(manifest, profile.max_payload_bytes + 32768)
    with lifecycle_lock(profile, time.monotonic() + profile.startup_wait_timeout_sec):
        if any((profile.nfs_root / queue / f"{rid}.json").exists() for queue in ("inbox", "processing", "failed")) or Path(manifest["input_path"]).exists():
            raise OsirisPayloadError("Duplicate request ID")
        try:
            records = []
            for name, source in sources.items():
                target = confined(staging / name, staging)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                records.append(dict(name=name, path=name, size=target.stat().st_size))
            body = dict(payload=payload, artifacts=records)
            json_bytes(body, profile.max_payload_bytes)
            client._atomic_json(body, Path(manifest["input_path"]))
            client._atomic_json(manifest, profile.nfs_root / "inbox" / f"{rid}.json")
        except Exception:
            cleanup(profile, session_id, rid)
            raise
    return rid


def cleanup(profile, session_id, rid, *, keep_artifacts=False):
    request_identity(rid)
    _, dirs = client.session_dirs(profile, session_id)
    for name in ("input", "output", "error"):
        (dirs[name] / f"{rid}.json").unlink(missing_ok=True)
    for queue in ("inbox", "processing", "failed"):
        (profile.nfs_root / queue / f"{rid}.json").unlink(missing_ok=True)
    for name in ("input", "output"):
        if name == "output" and keep_artifacts:
            continue
        directory = dirs[name] / f"{rid}.files"
        if directory.exists():
            confined(directory, dirs[name])
            shutil.rmtree(directory)


def wait(profile, session_id, rid, op, *, consumer_id="python", timeout_sec=1800,
         auto_recover=True):
    request_identity(rid)
    operation(profile, op)
    identity(consumer_id, "consumer ID")
    if not isinstance(timeout_sec, (float, int)) or not math.isfinite(timeout_sec) or timeout_sec <= 0:
        raise OsirisPayloadError("Invalid request timeout")
    _, dirs = client.session_dirs(profile, session_id)
    deadline = time.monotonic() + timeout_sec
    recovered = False
    complete = False
    try:
        while True:
            for kind in ("output", "error"):
                path = confined(dirs[kind] / f"{rid}.json", profile.nfs_root)
                if not path.exists():
                    continue
                response = read_json(path, profile.max_result_bytes)
                if not isinstance(response, dict) or any(response.get(key) != value for key, value in {
                    "request_id": rid, "request_type": op, "operation": op,
                    "service_name": profile.service_name, "session_id": session_id,
                    "consumer_id": consumer_id, "protocol_version": profile.protocol_version,
                }.items()):
                    raise OsirisRequestError("Osiris response correlation mismatch")
                if kind == "error":
                    from .errors import OsirisUnknownOperationError
                    error = {"unknown_operation": OsirisUnknownOperationError,
                             "invalid_payload": OsirisPayloadError}.get(response.get("error_code"), OsirisRequestError)
                    raise error(f"Worker rejected request {rid}")
                if "result" not in response:
                    raise OsirisRequestError("Osiris response has no result")
                artifacts = check_artifacts(response.get("artifacts", []), dirs["output"] / f"{rid}.files", profile.max_artifact_bytes)
                complete = True
                return InvocationResult(rid, response["result"], artifacts)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OsirisRequestTimeoutError(f"Osiris request timeout; request_id={rid}")
            if auto_recover and not recovered:
                heartbeat = profile.read_heartbeat()
                if not profile.heartbeat_live(heartbeat) or heartbeat.get("status") in {"stopped", "idle_stopping"}:
                    recovered = True
                    ensure_ready(profile, timeout=min(remaining, profile.startup_wait_timeout_sec))
            time.sleep(min(client.POLL_SEC, remaining))
    finally:
        cleanup(profile, session_id, rid, keep_artifacts=complete)
