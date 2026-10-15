"""Reusable NFS request transport for an Osiris service profile."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import pickle
import re
import time
import uuid
from pathlib import Path
from typing import Any

from .config import ServiceProfile
from .errors import OsirisRequestError, OsirisRequestTimeoutError
from .lifecycle import ensure_ready

logger = logging.getLogger(__name__)
POLL_SEC = 0.5
DEFAULT_REQUEST_TIMEOUT_SEC = 1800


def safe_session_id(session_id: Any) -> str:
    raw = str(session_id)
    readable = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("._-")[:64]
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
    return f"{readable or 'session'}__{digest}"


def session_dirs(profile: ServiceProfile, session_id: Any):
    safe = safe_session_id(session_id)
    root = profile.nfs_root / "sessions" / safe
    paths = {name: root / name for name in ("input", "output", "error")}
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return safe, paths


def _atomic_pickle(value: Any, target: Path):
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + f".tmp.{uuid.uuid4().hex}")
    try:
        with temporary.open("wb") as handle:
            pickle.dump(value, handle, protocol=pickle.HIGHEST_PROTOCOL)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_json(data: dict, target: Path):
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + f".tmp.{uuid.uuid4().hex}")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def submit_request(profile: ServiceProfile, session_id, request_type, query, payload,
                   timeout_sec=DEFAULT_REQUEST_TIMEOUT_SEC):
    if request_type not in profile.capabilities or timeout_sec <= 0:
        raise ValueError("Unsupported Osiris request type or timeout")
    profile.prepare_nfs()
    safe, dirs = session_dirs(profile, session_id)
    request_id = uuid.uuid4().hex
    now = time.time()
    manifest = {
        "protocol_version": profile.protocol_version, "service_name": profile.service_name,
        "request_id": request_id, "request_type": request_type,
        "session_id": str(session_id), "safe_session_id": safe, "query": str(query),
        "created_at": now, "expires_at": now + timeout_sec,
        "input_path": str(dirs["input"] / f"{request_id}.pkl"),
        "output_path": str(dirs["output"] / f"{request_id}.pkl"),
        "error_path": str(dirs["error"] / f"{request_id}.txt"),
    }
    input_path = Path(manifest["input_path"])
    _atomic_pickle(payload, input_path)
    try:
        _atomic_json(manifest, profile.nfs_root / "inbox" / f"{request_id}.json")
    except Exception:
        input_path.unlink(missing_ok=True)
        raise
    return request_id


def wait_result(profile: ServiceProfile, session_id, request_id, request_type,
                timeout_sec=DEFAULT_REQUEST_TIMEOUT_SEC, *, auto_recover=False):
    _, dirs = session_dirs(profile, session_id)
    output = dirs["output"] / f"{request_id}.pkl"
    error = dirs["error"] / f"{request_id}.txt"
    deadline = time.monotonic() + timeout_sec
    recovered = False
    try:
        while True:
            if output.exists():
                with output.open("rb") as handle:
                    response = pickle.load(handle)
                if (not isinstance(response, dict)
                        or response.get("request_id") != request_id
                        or response.get("request_type") != request_type):
                    raise OsirisRequestError("Osiris response correlation mismatch")
                return response["result"]
            if error.exists():
                detail = error.read_text(encoding="utf-8", errors="replace")
                logger.error("Osiris request %s failed: %s", request_id, detail)
                raise OsirisRequestError(f"Osiris {request_type} failed; request_id={request_id}")
            if time.monotonic() >= deadline:
                raise OsirisRequestTimeoutError(f"Osiris {request_type} timeout; request_id={request_id}")
            if auto_recover and not recovered:
                heartbeat = profile.read_heartbeat()
                if not profile.heartbeat_live(heartbeat) or heartbeat.get("status") in {"stopped", "idle_stopping"}:
                    recovered = True
                    logger.warning("Osiris worker lost during request %s; one restart attempt", request_id)
                    ensure_ready(profile)
            time.sleep(min(POLL_SEC, max(0, deadline - time.monotonic())))
    finally:
        for path in (output, error, dirs["input"] / f"{request_id}.pkl",
                     profile.nfs_root / "inbox" / f"{request_id}.json",
                     profile.nfs_root / "processing" / f"{request_id}.json",
                     profile.nfs_root / "failed" / f"{request_id}.json"):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Cannot clean Osiris request artifact: %s", path)


def request(profile: ServiceProfile, session_id, request_type, query, payload,
            timeout_sec=DEFAULT_REQUEST_TIMEOUT_SEC):
    """Startup and request deadlines are independent; recover one post-ready death."""
    ensure_ready(profile)
    request_id = submit_request(profile, session_id, request_type, query, payload, timeout_sec)
    return wait_result(profile, session_id, request_id, request_type, timeout_sec, auto_recover=True)
