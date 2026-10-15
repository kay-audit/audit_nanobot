"""Single physical worker, serial GPU execution, independent trusted logical adapters."""
from __future__ import annotations

import importlib.util
import pickle
import queue
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

from . import client
from .deployment import correlation, validate_request
from .dispatcher import process_json_request
from .lifecycle import lifecycle_lock
from .protocol import confined, read_json
from .worker import WorkerActivity, claim_new_requests, recover_processing_after_restart


def load_skill(name, root):
    """Namespace each skill package; never add a generic `utils` to sys.path."""
    package = "osiris_domain_" + name
    if package not in sys.modules:
        spec = importlib.util.spec_from_file_location(package, root / "__init__.py",
                                                      submodule_search_locations=[str(root)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[package] = module
        spec.loader.exec_module(module)
    return importlib.import_module(package + ".rerank_osiris_worker")


class ModelPool:
    """Share only identical keys; park incompatible weights on CPU, execute on CUDA only.

    Dtype/constructor policy is part of the caller-owned key. The proxy never
    exposes a CPU inference path. This bounds GPU residency to one model per kind.
    """

    def __init__(self):
        self.models = {}
        self.active = {}
        self.lock = threading.RLock()

    def proxy(self, kind, key, factory):
        pool = self
        class Proxy:
            def __getattr__(self, method):
                if method not in {"encode", "predict"}:
                    raise AttributeError(method)
                def invoke(*args, **kwargs):
                    with pool.lock:
                        current = pool.active.get(kind)
                        full = (kind, key)
                        if current != full:
                            if current in pool.models:
                                getattr(pool.models[current], "model", pool.models[current]).to("cpu")
                                import torch
                                torch.cuda.empty_cache()
                            if full not in pool.models:
                                pool.models[full] = factory()
                            else:
                                getattr(pool.models[full], "model", pool.models[full]).to("cuda:0")
                            pool.active[kind] = full
                        return getattr(pool.models[full], method)(*args, **kwargs)
                return invoke
        return Proxy()


class SharedWorker:
    def __init__(self, deployment, profiles, adapters, *, generation):
        self.deployment = deployment
        self.profile = deployment.profile
        self.profiles = {p.service_name: p for p in profiles}
        if not self.profiles or set(self.profiles) != set(adapters):
            raise ValueError("Every declared logical service needs its own adapter")
        for profile in profiles:
            if profile.deployment != deployment:
                raise ValueError("Worker received a foreign deployment")
        self.adapters = adapters
        self.generation = generation
        metadata = read_json(self.profile.metadata_path, 32768)
        self.activity = WorkerActivity(replace(self.profile, idle_timeout_sec=float(
            metadata.get("idle_timeout_sec", self.profile.idle_timeout_sec))))
        self.tasks = queue.Queue()
        self.execution = threading.Lock()

    def prepare(self):
        self.profile.prepare_nfs()
        for profile in self.profiles.values():
            profile.prepare_nfs()
            recover_processing_after_restart(profile)

    def scan(self):
        for profile in self.profiles.values():
            claim_new_requests(profile, self.tasks)

    def snapshot(self):
        result = self.activity.snapshot(self.tasks.qsize(), self.generation)
        result["logical_services"] = {name: list(p.capabilities) for name, p in self.profiles.items()}
        return result

    def step(self):
        # Also safe when a test/embedding application drives step from threads.
        with self.execution:
            self.scan()
            try:
                path = self.tasks.get_nowait()
            except queue.Empty:
                return False
            self.activity.begin()
            try:
                profile = next((p for p in self.profiles.values()
                                if path.parent.resolve() == (p.nfs_root / "processing").resolve()), None)
                if profile is None:
                    raise ValueError("Queue item belongs to no logical service")
                manifest = read_json(confined(path, profile.nfs_root, must_exist=True), profile.max_payload_bytes + 32768)
                # First verify the entire namespace; only then may we write a correlated error.
                validate_request(profile, manifest, manifest.get("generation"))
                if manifest["generation"] != self.generation:
                    self._error(profile, manifest, "worker_generation_changed")
                elif manifest.get("serialization") == "json":
                    process_json_request(profile, path, self.adapters[profile.service_name][0])
                else:
                    self._legacy(profile, manifest, self.adapters[profile.service_name][1])
            except Exception:
                # Invalid/expired envelopes are quarantined, never dispatched by guessing.
                if path.exists():
                    path.replace(path.parent.parent / "failed" / path.name)
            finally:
                self.activity.end()
                self.tasks.task_done()
            return True

    def _error(self, profile, manifest, code):
        if time.time() < manifest["expires_at"]:
            client._atomic_json(dict(correlation(manifest), error_code=code), Path(manifest["error_path"]))
        Path(manifest["input_path"]).unlink(missing_ok=True)
        (profile.nfs_root / "processing" / (manifest["request_id"] + ".json")).unlink(missing_ok=True)

    def _legacy(self, profile, manifest, handler):
        try:
            source = confined(Path(manifest["input_path"]), profile.nfs_root, must_exist=True)
            with source.open("rb") as stream:
                payload = pickle.load(stream)  # Trusted internal Python API only.
            result = handler(manifest, payload)
            if time.time() < manifest["expires_at"]:
                client._atomic_pickle(dict(correlation(manifest), result=result), Path(manifest["output_path"]))
        except Exception:
            self._error(profile, manifest, "handler_failed")
        finally:
            Path(manifest["input_path"]).unlink(missing_ok=True)
            (profile.nfs_root / "processing" / (manifest["request_id"] + ".json")).unlink(missing_ok=True)

    def idle_stop(self):
        with lifecycle_lock(self.profile, time.monotonic() + self.profile.startup_wait_timeout_sec):
            self.scan()
            if self.activity.idle_expired(self.tasks.qsize()):
                self.activity.stopping()
                return True
            return False

    def run(self, stop=None):
        stop = stop or threading.Event()
        # A second physical worker cannot recover or execute another worker's processing files.
        owner = replace(self.profile, nfs_root=self.profile.nfs_root / "worker-owner")
        with lifecycle_lock(owner, time.monotonic() + self.profile.startup_wait_timeout_sec):
            self.prepare()
            self.activity.ready()
            def heartbeat():
                while not stop.is_set():
                    client._atomic_json(self.snapshot(), self.profile.heartbeat_path)
                    stop.wait(.5)
            thread = threading.Thread(target=heartbeat, daemon=True)
            thread.start()
            try:
                while not stop.is_set():
                    if not self.step():
                        if self.idle_stop():
                            break
                        stop.wait(.05)
            finally:
                stop.set()
                thread.join(timeout=2)
                self.activity.stopped()
                client._atomic_json(self.snapshot(), self.profile.heartbeat_path)


def main():
    from .deployment import load_deployment
    from .registry import ServiceRegistry
    deployment, declarations = load_deployment()
    registry = ServiceRegistry()
    registry.register_deployment(deployment)
    root = Path(__file__).resolve().parents[3]
    pool = ModelPool()
    adapters = {}
    for name, declaration in declarations.items():
        domain = load_skill(name, root / declaration["skill_root"])
        profile = domain.PROFILE if hasattr(domain, "PROFILE") else domain.SERVICE
        registry.register(profile)
        adapters[name] = domain.create_shared_handlers(pool)
    metadata = read_json(deployment.profile.metadata_path, 32768)
    SharedWorker(deployment, registry.profiles(), adapters, generation=metadata["generation"]).run()
