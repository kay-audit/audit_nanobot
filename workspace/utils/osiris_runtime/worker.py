"""Reusable idle accounting and durable inbox recovery for service workers."""
from __future__ import annotations

import json
import os
import queue
import threading
import time
from pathlib import Path

from .config import ServiceProfile


class WorkerActivity:
    def __init__(self, profile: ServiceProfile):
        self.profile = profile
        self._lock = threading.Lock()
        self._status = "starting"
        self._active = 0
        self._last_mono = time.monotonic()
        self._last_wall = time.time()

    def ready(self):
        with self._lock:
            self._status = "ready"
            self._last_mono = time.monotonic()
            self._last_wall = time.time()

    def begin(self):
        with self._lock:
            self._active += 1
            self._status = "busy"

    def end(self):
        with self._lock:
            self._active -= 1
            self._last_mono = time.monotonic()
            self._last_wall = time.time()
            self._status = "ready"

    def stopping(self):
        with self._lock:
            self._status = "idle_stopping"

    def stopped(self):
        with self._lock:
            self._status = "stopped"

    def snapshot(self, queue_size: int, generation: str | None = None) -> dict:
        with self._lock:
            idle_for = 0.0 if self._active else max(0.0, time.monotonic() - self._last_mono)
            ready = self._status in {"ready", "busy"}
            return {
                "timestamp": time.time(), "pid": os.getpid(), "status": self._status,
                "service_name": self.profile.service_name, "generation": generation,
                "protocol_version": self.profile.protocol_version,
                "capabilities": list(self.profile.capabilities),
                "ready_gpus": self.profile.num_gpus if ready else 0,
                "visible_gpus": self.profile.num_gpus,
                "ready_devices": list(range(self.profile.num_gpus)) if ready else [],
                "queue_size": queue_size, "active_requests": self._active,
                "busy": bool(self._active), "last_activity_at": self._last_wall,
                "idle_timeout_sec": self.profile.idle_timeout_sec,
                "idle_for_sec": idle_for,
            }

    def idle_expired(self, queue_size: int) -> bool:
        with self._lock:
            return (self._status == "ready" and self._active == 0 and queue_size == 0
                    and time.monotonic() - self._last_mono >= self.profile.idle_timeout_sec)


def _discard_expired(path: Path):
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if time.time() < float(manifest["expires_at"]):
            return False
        path.unlink(missing_ok=True)
        return True
    except (OSError, ValueError, TypeError, KeyError):
        return False


def claim_new_requests(profile: ServiceProfile, target: queue.Queue):
    inbox, processing = profile.nfs_root / "inbox", profile.nfs_root / "processing"
    for path in sorted(inbox.glob("*.json")):
        if _discard_expired(path):
            continue
        claimed = processing / path.name
        try:
            os.replace(path, claimed)
        except FileNotFoundError:
            continue
        target.put(claimed)


def recover_processing_after_restart(profile: ServiceProfile):
    for path in (profile.nfs_root / "processing").glob("*.json"):
        if _discard_expired(path):
            continue
        os.replace(path, profile.nfs_root / "inbox" / path.name)


def should_idle_stop(profile: ServiceProfile, activity: WorkerActivity, target: queue.Queue) -> bool:
    """Re-scan before shutdown; a late manifest is recovered by the next job."""
    claim_new_requests(profile, target)
    if not activity.idle_expired(target.qsize()):
        return False
    claim_new_requests(profile, target)
    if target.qsize() or any((profile.nfs_root / "inbox").glob("*.json")):
        return False
    if not activity.idle_expired(target.qsize()):
        return False
    activity.stopping()
    return True
