"""Trusted profile registration; independent of every skill and the SDK."""
from __future__ import annotations

import importlib.util
import threading
from pathlib import Path

from .config import ServiceProfile
from .errors import OsirisServiceNotFoundError


class ServiceRegistry:
    def __init__(self):
        self._profiles: dict[str, ServiceProfile] = {}
        self._lock = threading.RLock()

    def register(self, profile: ServiceProfile) -> ServiceProfile:
        if not isinstance(profile, ServiceProfile):
            raise TypeError("Expected ServiceProfile")
        root = profile.nfs_root.resolve()
        with self._lock:
            current = self._profiles.get(profile.service_name)
            if current == profile:
                return current
            for existing in self._profiles.values():
                other = existing.nfs_root.resolve()
                job = profile.job_name.casefold().replace("-", "_")
                other_job = existing.job_name.casefold().replace("-", "_")
                if (existing.service_name == profile.service_name
                        or job == other_job or job.endswith("_" + other_job) or other_job.endswith("_" + job)
                        or root.is_relative_to(other) or other.is_relative_to(root)):
                    raise ValueError("Conflicting Osiris service name, job or NFS namespace")
            self._profiles[profile.service_name] = profile
            return profile

    def get(self, name: str) -> ServiceProfile:
        with self._lock:
            try:
                return self._profiles[name]
            except (KeyError, TypeError) as exc:
                raise OsirisServiceNotFoundError("Osiris service is not registered") from exc

    def profiles(self) -> tuple[ServiceProfile, ...]:
        with self._lock:
            return tuple(self._profiles.values())

    def load(self, path: Path) -> ServiceProfile:
        """Execute an operator-owned config file; never a Tool argument."""
        path = Path(path).resolve(strict=True)
        spec = importlib.util.spec_from_file_location("osiris_trusted_profile", path)
        if spec is None or spec.loader is None:
            raise ValueError("Cannot load Osiris profile")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return self.register(module.SERVICE)


services = ServiceRegistry()
