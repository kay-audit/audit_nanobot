"""Bounded JSON envelopes and confined NFS artifact references (protocol v2)."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

from .errors import OsirisPayloadError, OsirisUnknownOperationError


def json_bytes(value, limit: int) -> bytes:
    def visit(item, depth=0):
        if depth > 64:
            raise OsirisPayloadError("JSON nesting limit exceeded")
        if item is None or isinstance(item, (str, bool, int)):
            return
        if isinstance(item, float) and math.isfinite(item):
            return
        if isinstance(item, list):
            for child in item:
                visit(child, depth + 1)
            return
        if isinstance(item, dict) and all(isinstance(key, str) for key in item):
            for child in item.values():
                visit(child, depth + 1)
            return
        raise OsirisPayloadError("Only finite JSON-compatible values are accepted")
    visit(value)
    try:
        data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, RecursionError) as exc:
        raise OsirisPayloadError("Invalid JSON") from exc
    if len(data) > limit:
        raise OsirisPayloadError("JSON size limit exceeded")
    return data


def read_json(path: Path, limit: int):
    if path.stat().st_size > limit:
        raise OsirisPayloadError("JSON file size limit exceeded")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise OsirisPayloadError("Invalid JSON file") from exc
    json_bytes(value, limit)
    return value


def identity(value: str, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise OsirisPayloadError(f"Invalid {label}")
    return value


def request_identity(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise OsirisPayloadError("Invalid request ID")
    return value


def operation(profile, name):
    if name not in profile.capabilities:
        raise OsirisUnknownOperationError("Operation is not registered for this service")


def confined(path: Path, root: Path, *, must_exist=False) -> Path:
    root = Path(root).resolve()
    candidate = Path(path).resolve(strict=must_exist)
    if not candidate.is_relative_to(root) or candidate == root:
        raise OsirisPayloadError("Artifact path escapes its namespace")
    if must_exist and not candidate.is_file():
        raise OsirisPayloadError("Artifact is not a regular file")
    return candidate


def request_paths(profile, manifest):
    from .client import session_dirs
    rid = request_identity(manifest.get("request_id"))
    if manifest.get("protocol_version") != profile.protocol_version:
        raise OsirisPayloadError("Protocol version mismatch")
    if manifest.get("service_name") != profile.service_name:
        raise OsirisPayloadError("Service identity mismatch")
    op = manifest.get("operation")
    operation(profile, op)
    if manifest.get("request_type") != op or manifest.get("serialization") != "json":
        raise OsirisPayloadError("Invalid operation envelope")
    identity(manifest.get("consumer_id"), "consumer ID")
    session = manifest.get("session_id")
    if not isinstance(session, str) or not session or len(session) > 4096:
        raise OsirisPayloadError("Invalid session ID")
    _, dirs = session_dirs(profile, session)
    expected = {"input_path": dirs["input"] / f"{rid}.json",
                "output_path": dirs["output"] / f"{rid}.json",
                "error_path": dirs["error"] / f"{rid}.json"}
    for key, path in expected.items():
        if Path(manifest.get(key, "")).resolve() != confined(path, profile.nfs_root):
            raise OsirisPayloadError("Manifest path mismatch")
    try:
        created, expires = float(manifest["created_at"]), float(manifest["expires_at"])
        if not math.isfinite(created) or not math.isfinite(expires) or expires <= created:
            raise ValueError()
    except (KeyError, TypeError, ValueError) as exc:
        raise OsirisPayloadError("Invalid request deadline") from exc
    return dirs, expected


def check_artifacts(records, root: Path, limit: int) -> dict[str, Path]:
    if not isinstance(records, list) or len(records) > 128:
        raise OsirisPayloadError("Invalid artifacts list")
    total = 0
    result = {}
    for record in records:
        if not isinstance(record, dict):
            raise OsirisPayloadError("Invalid artifact record")
        name = identity(record.get("name"), "artifact name")
        if name in result:
            raise OsirisPayloadError("Duplicate artifact name")
        path = confined(root / record.get("path", ""), root, must_exist=True)
        size = path.stat().st_size
        if record.get("size") != size:
            raise OsirisPayloadError("Artifact size mismatch")
        total += size
        if total > limit:
            raise OsirisPayloadError("Artifact size limit exceeded")
        result[name] = path
    return result
