"""Operator-owned deployment configuration and strict shared request identity."""
from __future__ import annotations

import json
import os
import time
from dataclasses import replace
from pathlib import Path

from .config import PhysicalDeployment, ServiceProfile, common_timeout_settings
from .errors import OsirisBusyError, OsirisPayloadError, OsirisRequestError, OsirisRequestTimeoutError
from .protocol import confined, identity, read_json, request_identity


def load_deployment(path=None):
    root = Path(__file__).resolve().parents[3]
    path = Path(path or os.environ.get("OSIRIS_SHARED_CONFIG", root / "osiris_deployment.json"))
    data = json.loads(path.read_text(encoding="utf-8"))
    def expand(value):
        return str(value).replace("${PROJECT_ROOT}", str(root))
    settings = data["deployment"]
    physical = ServiceProfile(
        settings["name"], settings["job_name"], settings["image"], settings["pool"],
        expand(settings["worker_script"]), 1, Path(expand(settings["nfs_root"])),
        ("dispatch",), **common_timeout_settings(),
    )
    deployment = PhysicalDeployment(physical, settings.get("max_queue", 128))
    return deployment, data["services"]


def bind_profile(legacy: ServiceProfile):
    """Absence means explicit standalone; a present invalid shared config fails closed."""
    root = Path(__file__).resolve().parents[3]
    path = Path(os.environ.get("OSIRIS_SHARED_CONFIG", root / "osiris_deployment.json"))
    if not path.exists() and "OSIRIS_SHARED_CONFIG" not in os.environ:
        return legacy
    deployment, services = load_deployment(path)
    if legacy.service_name not in services:
        raise ValueError("Logical service is not declared in the shared deployment")
    physical = deployment.profile
    return replace(legacy, deployment=deployment, nfs_root=physical.nfs_root / "services" / legacy.service_name,
                   **{key: getattr(physical, key) for key in (
                       "job_name", "image", "pool", "worker_script", "num_gpus", "idle_timeout_sec")})


def fields(profile, manifest):
    """Called under the physical lifecycle lock before publishing the manifest."""
    if profile.deployment is None:
        return
    physical = profile.deployment.profile
    metadata = read_json(physical.metadata_path, 32768)
    generation = metadata.get("generation")
    request_identity(generation)
    pending = sum(1 for area in ("inbox", "processing")
                  for _ in (physical.nfs_root / "services").glob(f"*/{area}/*.json"))
    if pending >= profile.deployment.max_queue:
        raise OsirisBusyError("Shared GPU queue is full; retry after existing requests complete")
    manifest.update(deployment_id=physical.service_name, generation=generation,
                    consumer_id=manifest.get("consumer_id", "python"),
                    operation=manifest["request_type"])
    # Never recycle a UUID after timeout/cleanup: an in-flight handler may still
    # finish later. An empty durable tombstone contains no user data and is not
    # removed by request cleanup or worker recovery.
    accepted = physical.nfs_root / "accepted"
    accepted.mkdir(exist_ok=True)
    marker = confined(accepted / (manifest["request_id"] + ".seen"), physical.nfs_root)
    try:
        with marker.open("xb"):
            pass
    except FileExistsError as exc:
        raise OsirisPayloadError("Request ID was already accepted; use a fresh UUID") from exc


def ticket(profile, manifest):
    from .client import _atomic_json, session_dirs
    if profile.deployment is not None:
        _, dirs = session_dirs(profile, manifest["session_id"])
        _atomic_json(manifest, dirs["input"] / (manifest["request_id"] + ".identity.json"))


def correlation(manifest):
    return {key: manifest[key] for key in (
        "deployment_id", "service_name", "session_id", "consumer_id", "request_id",
        "request_type", "operation", "generation", "protocol_version",
    )}


def owns_request(profile, session, rid):
    from .client import session_dirs
    _, dirs = session_dirs(profile, session)
    path = confined(dirs["input"] / (rid + ".identity.json"), profile.nfs_root)
    if not path.is_file():
        return False
    manifest = read_json(path, profile.max_payload_bytes + 32768)
    return (manifest.get("session_id") == str(session) and manifest.get("request_id") == rid
            and manifest.get("service_name") == profile.service_name
            and manifest.get("deployment_id") == profile.deployment.profile.service_name)


def validate_response(profile, session, rid, op, response, consumer=None):
    if profile.deployment is None:
        return
    from .client import session_dirs
    _, dirs = session_dirs(profile, session)
    manifest = read_json(confined(dirs["input"] / (rid + ".identity.json"),
                                  profile.nfs_root, must_exist=True), profile.max_payload_bytes + 32768)
    expected = correlation(manifest)
    if time.time() >= manifest["expires_at"]:
        raise OsirisRequestTimeoutError("Shared request deadline expired")
    if (manifest["session_id"] != str(session) or manifest["request_id"] != rid
            or manifest["operation"] != op or (consumer is not None and manifest["consumer_id"] != consumer)
            or not isinstance(response, dict) or any(response.get(k) != v for k, v in expected.items())):
        raise OsirisRequestError("Shared response identity mismatch")
    metadata = read_json(profile.metadata_path, 32768)
    if metadata.get("generation") != manifest["generation"]:
        raise OsirisRequestError("Worker generation changed; retry this request explicitly")


def validate_request(profile, manifest, generation):
    from .client import session_dirs
    if manifest.get("service_name") != profile.service_name:
        raise OsirisPayloadError("Logical service mismatch")
    request_identity(manifest.get("request_id"))
    identity(manifest.get("consumer_id"), "consumer")
    if (manifest.get("deployment_id") != profile.deployment.profile.service_name
            or manifest.get("protocol_version") != profile.protocol_version
            or manifest.get("operation") != manifest.get("request_type")
            or manifest.get("request_type") not in profile.capabilities):
        raise OsirisPayloadError("Shared request identity mismatch")
    session = manifest.get("session_id")
    if not isinstance(session, str) or not session or len(session) > 4096:
        raise OsirisPayloadError("Invalid session")
    _, dirs = session_dirs(profile, session)
    rid = manifest["request_id"]
    retained = read_json(confined(dirs["input"] / (rid + ".identity.json"),
                                  profile.nfs_root, must_exist=True), profile.max_payload_bytes + 32768)
    if retained != manifest:
        raise OsirisPayloadError("Queue manifest differs from the accepted request identity")
    suffixes = (".json",) * 3 if manifest.get("serialization") == "json" else (".pkl", ".pkl", ".txt")
    for area, suffix in zip(("input", "output", "error"), suffixes):
        expected = confined(dirs[area] / (rid + suffix), profile.nfs_root)
        if Path(manifest.get(area + "_path", "")).resolve() != expected:
            raise OsirisPayloadError("Shared artifact namespace mismatch")
    if manifest.get("generation") != generation:
        raise OsirisRequestError("Old generation request; explicit retry required")
    if not isinstance(manifest.get("expires_at"), (float, int)) or not time.time() < manifest["expires_at"]:
        raise OsirisRequestError("Request expired")
    return dirs


def trusted_session(session):
    """Python callers may supply a session; the live Nanobot context always wins."""
    import sys
    context = sys.modules.get("nanobot.agent.tools.context")
    if context is not None and context.current_request_context() is not None:
        current = context.current_request_session_key()
        if not current:
            raise OsirisPayloadError("Nanobot request context has no session")
        return current
    return session
