"""Shared, stdlib-only configuration and heartbeat contract for Appeals Osiris."""
from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path

from workspace.utils.osiris_runtime.config import ServiceProfile, common_timeout_settings
from workspace.utils.osiris_runtime.errors import OsirisUnavailableError

JOB_NAME = os.environ.get("APPEALS_OSIRIS_JOB_NAME", "srbd3")
POOL = os.environ.get("APPEALS_OSIRIS_POOL", "common")
NUM_GPUS = 1
WORKER_SCRIPT = os.environ.get(
    "APPEALS_OSIRIS_WORKER_SCRIPT",
    "/home/datalab/nfs/audit_nanobot/workspace/skills/appeals-analyzer/rerank_osiris_worker.py",
)
NFS_ROOT = Path(os.environ.get(
    "APPEALS_OSIRIS_NFS_ROOT",
    "/home/datalab/nfs/audit_nanobot/workspace/data_store/osiris/srb_d3",
))
OSIRIS_IMAGE = os.environ.get("APPEALS_OSIRIS_IMAGE") or (
    "registry.ca.sbrf.ru/ci02684173/ci02697916/"
    "notebooks/python3.12/cuda12.4/d-04.000.00:d-04.000.00-geometric"
)
PROTOCOL_VERSION = 2
CAPABILITIES = ("retrieve", "rerank", "embed")
HEARTBEAT_MAX_AGE_SEC = 12.0
HEARTBEAT_PATH = NFS_ROOT / "worker_heartbeat.json"
JOB_META_PATH = NFS_ROOT / "osiris_job.json"
LAUNCH_LOCK_PATH = NFS_ROOT / "launcher.lock"

SERVICE = ServiceProfile(
    service_name="appeals",
    job_name=JOB_NAME,
    image=OSIRIS_IMAGE,
    pool=POOL,
    worker_script=WORKER_SCRIPT,
    num_gpus=NUM_GPUS,
    nfs_root=NFS_ROOT,
    capabilities=CAPABILITIES,
    protocol_version=PROTOCOL_VERSION,
    heartbeat_max_age_sec=HEARTBEAT_MAX_AGE_SEC,
    allow_legacy_heartbeat_without_service_name=True,
    **common_timeout_settings(),
)


class OsirisWorkerNotReady(OsirisUnavailableError):
    """Operator-actionable error safe to show in the native Tool response."""


def prepare_nfs(root: Path = NFS_ROOT) -> None:
    for path in (root, *(root / name for name in ("inbox", "processing", "failed", "sessions"))):
        path.mkdir(parents=True, exist_ok=True)


def read_heartbeat(path: Path = HEARTBEAT_PATH):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def heartbeat_age(heartbeat):
    try:
        age = time.time() - float(heartbeat["timestamp"])
        return age if math.isfinite(age) else None
    except (KeyError, TypeError, ValueError):
        return None


def heartbeat_ready(heartbeat) -> bool:
    age = heartbeat_age(heartbeat)
    if age is None or not 0 <= age <= HEARTBEAT_MAX_AGE_SEC:
        return False
    try:
        capabilities = heartbeat.get("capabilities")
        return (heartbeat.get("status") == "ready"
                and heartbeat.get("protocol_version") == PROTOCOL_VERSION
                and int(heartbeat.get("ready_gpus", 0)) == NUM_GPUS
                and isinstance(capabilities, (list, tuple))
                and all(name in capabilities for name in CAPABILITIES))
    except (TypeError, ValueError):
        return False
