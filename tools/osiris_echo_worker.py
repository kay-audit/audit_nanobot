"""Minimal worker example: python tools/osiris_echo_worker.py --nfs-root <path>.

No SDK, CUDA or ML imports. An operator creates the GPU job separately when
deploying this example; local tests call its handler through temporary NFS.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workspace.utils.osiris_runtime import HandlerRegistry, ServiceProfile, run_worker


def echo(payload, context):
    return {"echo": payload, "consumer": context.consumer_id, "session": context.session_id}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nfs-root", type=Path, required=True)
    parser.add_argument("--idle-timeout", type=float, default=28800)
    args = parser.parse_args()
    profile = ServiceProfile("echo", "echo-job", "operator-image", "common", __file__,
                             1, args.nfs_root, ("echo",), idle_timeout_sec=args.idle_timeout)
    run_worker(profile, HandlerRegistry().register("echo", echo))


if __name__ == "__main__":
    main()
