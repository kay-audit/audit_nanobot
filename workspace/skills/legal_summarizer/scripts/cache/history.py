"""History operation checkpoints in the existing skill cache, session isolated."""
from __future__ import annotations

import hashlib
import json
import re
import time
from contextlib import contextmanager
from pathlib import Path

from cache.manifest import _atomic_write_json, _read_json, manifest_root

PIPELINE_VERSION = "history-semantic-v1"


def fingerprint(parsed, cfg, context, policy, prompt):
    # Credential values are neither persisted nor included in identity.
    payload = {"version": PIPELINE_VERSION, "sources": parsed["source_hashes"],
               "target": parsed["target"], "overview": parsed["overview"],
               "llm": {k: cfg.get(k) for k in ("model", "provider", "api_base")},
               "context": context, "policy": policy, "prompt": prompt}
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return "history_" + digest[:32]


def directory(root, session, operation_id):
    if not re.fullmatch(r"history_[0-9a-f]{32}", operation_id):
        raise ValueError("Invalid history operation_id")
    if not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9._-]*", session) or session in (".", ".."):
        raise ValueError("Invalid session key")
    path = manifest_root(root) / "history" / session / operation_id
    if path.resolve() != path.absolute():
        raise ValueError("Перенаправленный каталог history запрещён")
    return path


@contextmanager
def file_lock(path: Path, *, blocking: bool = True):
    """OS advisory lock: released on crash, no stale PID ownership guesses."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    acquired = False
    try:
        try:
            if __import__("os").name == "nt":
                import msvcrt
                handle.seek(0)
                if not handle.read(1):
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                while True:
                    try:
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if not blocking:
                            raise
                        time.sleep(.1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            acquired = True
        except (BlockingIOError, OSError):
            if blocking:
                raise
        yield acquired
    finally:
        if acquired:
            if __import__("os").name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def read(path):
    if path.resolve() != path.absolute():
        raise ValueError("Перенаправленный checkpoint запрещён")
    return _read_json(path)


def llm_lock(root):
    return file_lock(manifest_root(root) / "history" / "llm.lock")


def write(path, payload):
    if path.resolve() != path.absolute():
        raise ValueError("Перенаправленный checkpoint запрещён")
    for attempt in range(8):
        try:
            _atomic_write_json(path, payload)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(min(.01 * 2 ** attempt, .1))
