"""One Osiris lifecycle implementation for operator CLI and runtime callers."""
from __future__ import annotations

import importlib
import json
import os
import re
import socket
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .config import ServiceProfile
from .errors import OsirisStartupTimeoutError, OsirisUnavailableError

TERMINAL = {"not_found", "finished", "failed", "stopped"}
POLL_SEC = 1.0


def import_osiris():
    clients = "/opt/clients"
    if clients not in sys.path:
        sys.path.insert(0, clients)
    try:
        return importlib.import_module("osiris")
    except Exception as exc:
        raise OsirisUnavailableError("Osiris SDK is unavailable") from exc


def _atomic_json(data: dict, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp.{uuid.uuid4().hex}")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_meta(profile: ServiceProfile) -> dict:
    try:
        value = json.loads(profile.metadata_path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) and value.get("requested_name") == profile.job_name else {}
    except (OSError, ValueError):
        return {}


def _write_meta(profile: ServiceProfile, **values):
    _atomic_json({"requested_name": profile.job_name, "service_name": profile.service_name,
                  "updated_at": time.time(), **values}, profile.metadata_path)


def _create_name(result: Any) -> str | None:
    if isinstance(result, dict):
        return next((str(result[key]) for key in ("job", "job_name", "name") if result.get(key)), None)
    return result.strip() if isinstance(result, str) and result.strip() else None


def _names(value: Any) -> list[str]:
    found = []
    def visit(item):
        if isinstance(item, str):
            if item.strip():
                found.append(item.strip())
        elif isinstance(item, dict):
            for key in ("job", "job_name", "name"):
                if isinstance(item.get(key), str):
                    found.append(item[key].strip())
            for nested in item.values():
                if isinstance(nested, (dict, list, tuple, set)):
                    visit(nested)
        elif isinstance(item, (list, tuple, set)):
            for nested in item:
                visit(nested)
    visit(value)
    return list(dict.fromkeys(name for name in found if name))


def classify_state(raw: Any) -> str:
    if isinstance(raw, dict):
        for key in ("state", "phase", "status"):
            if key in raw:
                return classify_state(raw[key])
        return "unknown"
    if not isinstance(raw, str):
        return "unknown"
    value = raw.strip().lower()
    if "not found" in value or "not_found" in value:
        return "not_found"
    if value in {"pending", "queued", "scheduling", "starting"}:
        return "pending"
    if value == "running":
        return "running"
    if value in {"failed", "failure", "crashloop"}:
        return "failed"
    if value in {"succeeded", "completed", "complete", "finished"}:
        return "finished"
    if value in {"stopped", "terminated", "cancelled", "canceled", "deleted"}:
        return "stopped"
    return "unknown"


def query_state(osiris, job_name: str):
    details = []
    for name in ("state", "status"):
        method = getattr(osiris, name, None)
        if method is None:
            continue
        try:
            raw = method(job_name)
        except Exception as exc:
            raw = str(exc)
        state = classify_state(raw)
        if state != "unknown":
            return state, raw
        details.append(raw)
    return "unknown", details


def _matches(profile: ServiceProfile, name: str) -> bool:
    if name == profile.job_name:
        return True
    pattern = r"(?i)([-_])" + "[-_]".join(re.escape(part) for part in re.split("[-_]", profile.job_name)) + "$"
    return re.search(pattern, name, re.IGNORECASE) is not None


def discover(profile: ServiceProfile, osiris):
    """Return actual name, state, details; never interpret SDK uncertainty as absence."""
    meta = _read_meta(profile)
    actual = meta.get("job_name")
    candidates = {}
    if actual:
        candidates[str(actual)] = query_state(osiris, str(actual))
    try:
        listed = osiris.list()
    except Exception as exc:
        if actual and candidates[str(actual)][0] not in TERMINAL:
            return str(actual), *candidates[str(actual)]
        return actual, "unknown", str(exc)
    if not isinstance(listed, (dict, list, tuple, set)):
        return actual, "unknown", listed
    names = _names(listed)
    if isinstance(listed, dict) and listed and not names:
        if not any(key in listed and listed[key] == [] for key in ("jobs", "items", "data", "results")):
            return actual, "unknown", listed
    for name in names:
        if _matches(profile, name) and name not in candidates:
            candidates[name] = query_state(osiris, name)
    live = [(name, state, raw) for name, (state, raw) in candidates.items() if state not in TERMINAL]
    if len(live) > 1:
        return None, "unknown", f"Multiple matching jobs: {[item[0] for item in live]}"
    if live:
        return live[0]
    if meta.get("create_pending") and not actual:
        return None, "unknown", "Unconfirmed create without actual job name"
    if actual:
        return str(actual), *candidates[str(actual)]
    return None, "not_found", listed


def _owner_gone(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("host") != socket.gethostname() or not isinstance(data.get("pid"), int):
            return False
        os.kill(data["pid"], 0)
        return False
    except ProcessLookupError:
        return True
    except (OSError, ValueError, TypeError):
        return False


@contextmanager
def lifecycle_lock(profile: ServiceProfile, deadline: float):
    profile.prepare_nfs()
    path = profile.lock_path
    while True:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            break
        except FileExistsError:
            if _owner_gone(path):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise OsirisStartupTimeoutError("Osiris lifecycle lock remained occupied")
            time.sleep(min(POLL_SEC, max(0, deadline - time.monotonic())))
    token = uuid.uuid4().hex
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"pid": os.getpid(), "host": socket.gethostname(),
                       "created_at": time.time(), "token": token}, handle)
        yield
    finally:
        try:
            if json.loads(path.read_text(encoding="utf-8")).get("token") == token:
                path.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass


def _ready_for_job(profile: ServiceProfile, name: str | None, state: str, meta: dict) -> bool:
    if not name or state != "running":
        return False
    heartbeat = profile.read_heartbeat()
    if not profile.heartbeat_ready(heartbeat):
        return False
    generation = meta.get("generation")
    return not generation or heartbeat.get("generation") == generation


def _start_under_lock(profile: ServiceProfile, osiris):
    name, state, details = discover(profile, osiris)
    if state == "unknown":
        return name, state, details, False
    if state not in TERMINAL:
        return name, state, details, False
    if profile.heartbeat_ready(profile.read_heartbeat()) and state in TERMINAL:
        # A fresh heartbeat may be from a job stopping; SDK state wins once confirmed.
        pass
    if not Path(profile.worker_script).is_file():
        raise OsirisUnavailableError(f"Osiris worker script is missing: {profile.worker_script}")
    generation = uuid.uuid4().hex
    _write_meta(profile, create_pending=True, generation=generation, create_started_at=time.time(),
                idle_timeout_sec=profile.idle_timeout_sec)
    try:
        result = osiris.create(name=profile.job_name, image=profile.image, pool=profile.pool,
                               script=profile.worker_script, restart=False, num_nodes=1,
                               num_gpus=profile.num_gpus, type="pytorchjob")
    except Exception as exc:
        raise OsirisUnavailableError("Osiris create outcome is unconfirmed; inspect SDK status") from exc
    name = _create_name(result)
    _write_meta(profile, job_name=name, create_pending=True, generation=generation,
                create_started_at=time.time(), idle_timeout_sec=profile.idle_timeout_sec)
    return name, "pending", result, True


def _recycle_stale_running(profile: ServiceProfile, osiris, name: str, deadline: float) -> bool:
    """Delete a previously READY but now dead worker before any replacement create."""
    actual, state, _ = discover(profile, osiris)
    meta = _read_meta(profile)
    heartbeat = profile.read_heartbeat()
    if (actual != name or state != "running" or meta.get("create_pending")
            or meta.get("job_name") != name or profile.heartbeat_ready(heartbeat)):
        return False
    age = profile.heartbeat_age(heartbeat)
    stale_for = age if age is not None else time.time() - float(meta.get("updated_at", time.time()))
    if stale_for < 2 * profile.heartbeat_max_age_sec:
        return False
    osiris.delete(name)
    while True:
        state, _ = query_state(osiris, name)
        if state in TERMINAL:
            return True
        if time.monotonic() >= deadline:
            raise OsirisStartupTimeoutError("Stale Osiris job deletion was not confirmed")
        time.sleep(min(POLL_SEC, max(0, deadline - time.monotonic())))


def ensure_ready(profile: ServiceProfile, osiris=None, *, timeout: float | None = None) -> str:
    """Create at most once, share an NFS lock, then wait within one startup deadline."""
    timeout = profile.startup_wait_timeout_sec if timeout is None else timeout
    if timeout <= 0:
        raise ValueError("Startup timeout must be positive")
    deadline = time.monotonic() + timeout
    try:
        osiris = osiris if osiris is not None else import_osiris()
        created = False
        while True:
            with lifecycle_lock(profile, deadline):
                name, state, details, did_create = _start_under_lock(profile, osiris)
                created |= did_create
            while True:
                name, state, details = discover(profile, osiris)
                meta = _read_meta(profile)
                if _ready_for_job(profile, name, state, meta):
                    if meta.get("create_pending"):
                        _write_meta(profile, job_name=name, generation=meta.get("generation"),
                                    create_started_at=meta.get("create_started_at"),
                                    idle_timeout_sec=meta.get("idle_timeout_sec", profile.idle_timeout_sec))
                    return name
                if state == "running" and name and not created:
                    heartbeat = profile.read_heartbeat()
                    if not profile.heartbeat_live(heartbeat):
                        with lifecycle_lock(profile, deadline):
                            if _recycle_stale_running(profile, osiris, name, deadline):
                                break
                if state in TERMINAL:
                    if created:
                        if state == "not_found" and time.monotonic() < deadline:
                            # create() can return before scheduler discovery is visible.
                            time.sleep(min(POLL_SEC, max(0, deadline - time.monotonic())))
                            continue
                        raise OsirisUnavailableError(f"Osiris job ended before READY: {state}")
                    break
                if time.monotonic() >= deadline:
                    raise OsirisStartupTimeoutError(f"Osiris did not become READY within {timeout:g}s; state={state}")
                time.sleep(min(POLL_SEC, max(0, deadline - time.monotonic())))
            if time.monotonic() >= deadline:
                raise OsirisStartupTimeoutError(f"Osiris did not become READY within {timeout:g}s")
    except OsirisUnavailableError:
        raise
    except Exception as exc:
        raise OsirisUnavailableError(f"Osiris startup failed: {type(exc).__name__}") from exc


def service_status(profile: ServiceProfile, osiris=None) -> dict:
    osiris = osiris if osiris is not None else import_osiris()
    name, state, details = discover(profile, osiris)
    heartbeat = profile.read_heartbeat() or {}
    age = profile.heartbeat_age(heartbeat)
    ready = _ready_for_job(profile, name, state, _read_meta(profile))
    idle_for = heartbeat.get("idle_for_sec") if ready else None
    idle_timeout = heartbeat.get("idle_timeout_sec", profile.idle_timeout_sec)
    return {
        "requested job name": profile.job_name, "actual job name": name,
        "Osiris state": state, "heartbeat status": heartbeat.get("status"),
        "heartbeat age seconds": age, "heartbeat ready": ready,
        "worker PID": heartbeat.get("pid"), "protocol version": heartbeat.get("protocol_version"),
        "capabilities": heartbeat.get("capabilities"), "visible GPUs": heartbeat.get("visible_gpus"),
        "ready GPUs": heartbeat.get("ready_gpus"), "queue size": heartbeat.get("queue_size"),
        "active requests": heartbeat.get("active_requests"), "last activity": heartbeat.get("last_activity_at"),
        "idle timeout seconds": idle_timeout, "idle age seconds": idle_for,
        "idle remaining seconds": max(0, idle_timeout - idle_for) if isinstance(idle_for, (int, float)) else None,
        "auto stopped": state in TERMINAL and heartbeat.get("status") == "stopped",
    }


def stop_service(profile: ServiceProfile, osiris=None, *, timeout: float = 120.0) -> str | None:
    """Use the confirmed closed-contour osiris.delete API, then verify termination."""
    if timeout <= 0:
        raise ValueError("Stop timeout must be positive")
    osiris = osiris if osiris is not None else import_osiris()
    deadline = time.monotonic() + timeout
    with lifecycle_lock(profile, deadline):
        name, state, details = discover(profile, osiris)
        if state == "unknown":
            raise OsirisUnavailableError(f"Cannot confirm job state; metadata retained: {details}")
        if state not in TERMINAL:
            osiris.delete(name)
            while True:
                state, details = query_state(osiris, name)
                if state in TERMINAL:
                    break
                if time.monotonic() >= deadline:
                    raise OsirisUnavailableError("Stop was not confirmed; metadata retained")
                time.sleep(min(POLL_SEC, max(0, deadline - time.monotonic())))
        profile.metadata_path.unlink(missing_ok=True)
        profile.heartbeat_path.unlink(missing_ok=True)
        return name
