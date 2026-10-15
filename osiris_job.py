"""Operator CLI for any configured shared Osiris service profile."""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

from workspace.utils.osiris_runtime import ServiceProfile
from workspace.utils.osiris_runtime.api import OsirisRuntime
from workspace.utils.osiris_runtime.lifecycle import ensure_ready, service_status, stop_service
from workspace.utils.osiris_runtime.registry import ServiceRegistry

DEFAULT_CONFIG = Path(__file__).resolve().parent / "workspace/skills/appeals-analyzer/utils/osiris_config.py"


def load_profile(path: Path = DEFAULT_CONFIG) -> ServiceProfile:
    spec = importlib.util.spec_from_file_location("osiris_service_profile", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load Osiris profile: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    profile = getattr(module, "SERVICE", None)
    if not isinstance(profile, ServiceProfile):
        raise RuntimeError(f"Osiris profile file must export SERVICE: {path}")
    return profile


def main(argv=None, *, default_config: Path = DEFAULT_CONFIG):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-file", type=Path, default=default_config,
                        help="Python profile file exporting a ServiceProfile named SERVICE")
    parser.add_argument("--service", help="Registered service name from the trusted config")
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start")
    start.add_argument("--startup-timeout", type=float)
    commands.add_parser("status")
    stop = commands.add_parser("stop")
    stop.add_argument("--timeout", type=float, default=120.0)
    invoke = commands.add_parser("invoke")
    invoke.add_argument("--operation", required=True)
    invoke.add_argument("--payload", required=True, help="JSON payload")
    invoke.add_argument("--session", required=True)
    invoke.add_argument("--consumer", default="operator")
    invoke.add_argument("--query", default="")
    args = parser.parse_args(argv)
    try:
        profile = load_profile(args.config_file)
        registry = ServiceRegistry()
        if profile.deployment is not None:
            registry.register_deployment(profile.deployment)
        registry.register(profile)
        selected = args.service or profile.service_name
        profile = registry.get(selected)
        if args.command == "start":
            job = ensure_ready(profile, timeout=args.startup_timeout)
            print(f"Osiris worker READY\nservice={profile.service_name}\njob={job}\ngpu={profile.num_gpus}")
        elif args.command == "status":
            values = service_status(profile)
            for key, value in values.items():
                print(f"{key}: {value}")
            if values["Osiris state"] in {"not_found", "finished", "failed", "stopped"}:
                print("Osiris job is not running")
        elif args.command == "invoke":
            result = OsirisRuntime(registry).invoke(selected, args.operation, json.loads(args.payload),
                                                   session_id=args.session, consumer_id=args.consumer,
                                                   query=args.query)
            print(json.dumps({"request_id": result.request_id, "result": result.value,
                              "artifacts": {name: str(path) for name, path in result.artifacts.items()}}, ensure_ascii=False))
        else:
            job = stop_service(profile, timeout=args.timeout)
            print(f"Osiris job stopped or already absent: {job or profile.job_name}")
        return 0
    except (RuntimeError, TimeoutError, ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
