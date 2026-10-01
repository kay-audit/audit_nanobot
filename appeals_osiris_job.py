"""Compatibility operator CLI for the Appeals shared Osiris service."""
from __future__ import annotations

from osiris_job import DEFAULT_CONFIG, main as _main


def main(argv=None):
    return _main(argv, default_config=DEFAULT_CONFIG)


if __name__ == "__main__":
    raise SystemExit(main())
