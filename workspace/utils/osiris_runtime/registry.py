"""Trusted profile registration; independent of every skill and the SDK."""
from __future__ import annotations

import importlib.util
import threading
from pathlib import Path

from .config import PhysicalDeployment, ServiceProfile
from .errors import OsirisServiceNotFoundError


class ServiceRegistry:
    def __init__(self):
        self._profiles: dict[str, ServiceProfile] = {}
        self._deployments: dict[str, PhysicalDeployment] = {}
        self._lock = threading.RLock()

    def register_deployment(self, deployment: PhysicalDeployment):
        physical = deployment.profile
        with self._lock:
            current = self._deployments.get(physical.service_name)
            if current == deployment:
                return current
            for existing in (*self._profiles.values(), *(d.profile for d in self._deployments.values())):
                a, b = physical.nfs_root.resolve(), existing.nfs_root.resolve()
                if (physical.service_name == existing.service_name or physical.job_name == existing.job_name
                        or a.is_relative_to(b) or b.is_relative_to(a)):
                    raise ValueError("Conflicting physical deployment")
            self._deployments[physical.service_name] = deployment
        return deployment

    def register(self, profile: ServiceProfile) -> ServiceProfile:
        if not isinstance(profile, ServiceProfile):
            raise TypeError("Expected ServiceProfile")
        root = profile.nfs_root.resolve()
        with self._lock:
            if profile.deployment is not None:
                if self._deployments.get(profile.deployment.profile.service_name) != profile.deployment:
                    raise ValueError("Register the physical deployment before logical services")
            else:
                for deployment in self._deployments.values():
                    physical = deployment.profile
                    other = physical.nfs_root.resolve()
                    if (profile.job_name == physical.job_name or root.is_relative_to(other)
                            or other.is_relative_to(root)):
                        raise ValueError("Standalone service conflicts with a physical deployment")
            current = self._profiles.get(profile.service_name)
            if current == profile:
                return current
            for existing in self._profiles.values():
                other = existing.nfs_root.resolve()
                job = profile.job_name.casefold().replace("-", "_")
                other_job = existing.job_name.casefold().replace("-", "_")
                shared = profile.deployment is not None and profile.deployment == existing.deployment
                if (existing.service_name == profile.service_name
                        or (not shared and (job == other_job or job.endswith("_" + other_job) or other_job.endswith("_" + job)))
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
        if module.SERVICE.deployment is not None:
            self.register_deployment(module.SERVICE.deployment)
        return self.register(module.SERVICE)


services = ServiceRegistry()
