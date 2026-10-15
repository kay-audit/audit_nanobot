"""Lightweight reusable service definition and heartbeat contract."""
from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass
from pathlib import Path


def _positive_env(name: str, default: float) -> float:
    value = float(os.environ.get(name, default))
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return value


@dataclass(frozen=True)
class ServiceProfile:
    service_name: str
    job_name: str
    image: str
    pool: str
    worker_script: str
    num_gpus: int
    nfs_root: Path
    capabilities: tuple[str, ...]
    protocol_version: int = 2
    heartbeat_max_age_sec: float = 12.0
    idle_timeout_sec: float = 3600.0
    startup_wait_timeout_sec: float = 300.0
    retry_after_sec: float = 900.0
    allow_legacy_heartbeat_without_service_name: bool = False

    def __post_init__(self):
        if not self.service_name or not self.job_name or not self.worker_script:
            raise ValueError("Osiris service, job and worker script names are required")
        if self.num_gpus <= 0 or not self.capabilities:
            raise ValueError("Osiris GPU count and capabilities must be positive")
        for name in ("heartbeat_max_age_sec", "idle_timeout_sec", "startup_wait_timeout_sec", "retry_after_sec"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive and finite")

    @property
    def heartbeat_path(self) -> Path:
        return self.nfs_root / "worker_heartbeat.json"

    @property
    def metadata_path(self) -> Path:
        return self.nfs_root / "osiris_job.json"

    @property
    def lock_path(self) -> Path:
        return self.nfs_root / "launcher.lock"

    def prepare_nfs(self) -> None:
        for path in (self.nfs_root, *(self.nfs_root / part for part in ("inbox", "processing", "failed", "sessions"))):
            path.mkdir(parents=True, exist_ok=True)

    def read_heartbeat(self):
        import json
        try:
            value = json.loads(self.heartbeat_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except (OSError, ValueError):
            return None

    def heartbeat_age(self, heartbeat):
        try:
            age = time.time() - float(heartbeat["timestamp"])
            return age if math.isfinite(age) else None
        except (KeyError, TypeError, ValueError):
            return None

    def heartbeat_live(self, heartbeat) -> bool:
        if not isinstance(heartbeat, dict):
            return False
        age = self.heartbeat_age(heartbeat)
        if age is None or not 0 <= age <= self.heartbeat_max_age_sec:
            return False
        try:
            heartbeat_service = heartbeat.get("service_name")
            service_matches = (heartbeat_service == self.service_name or
                               (self.allow_legacy_heartbeat_without_service_name
                                and heartbeat_service is None))
            return (heartbeat.get("protocol_version") == self.protocol_version
                    and service_matches
                    and isinstance(heartbeat.get("capabilities"), (list, tuple))
                    and all(name in heartbeat["capabilities"] for name in self.capabilities)
                    and int(heartbeat.get("visible_gpus", 0)) >= self.num_gpus)
        except (TypeError, ValueError):
            return False

    def heartbeat_ready(self, heartbeat) -> bool:
        try:
            return (self.heartbeat_live(heartbeat)
                    and heartbeat.get("status") in {"ready", "busy"}
                    and int(heartbeat.get("ready_gpus", 0)) >= self.num_gpus)
        except (TypeError, ValueError):
            return False


def common_timeout_settings():
    return {
        "idle_timeout_sec": _positive_env("OSIRIS_IDLE_TIMEOUT_SEC", 3600),
        "startup_wait_timeout_sec": _positive_env("OSIRIS_START_WAIT_TIMEOUT_SEC", 300),
        "retry_after_sec": _positive_env("OSIRIS_RETRY_AFTER_SEC", 900),
    }
