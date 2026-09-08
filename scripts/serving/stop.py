"""scripts.serving.stop - CLI для остановки запущенного sglang/ollama.

Использование:
    python -m scripts.serving.stop              # стоп sglang + ollama (если есть)
    python -m scripts.serving.stop --mode sglang
    python -m scripts.serving.stop --mode ollama
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import load_serving_config
from .ollama_launcher import stop as stop_ollama
from .sglang_launcher import stop as stop_sglang


def main() -> int:
    parser = argparse.ArgumentParser(description="Остановить локальный LLM-сервер (sglang/ollama)")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Путь к config.json (по умолчанию корень проекта)",
    )
    parser.add_argument(
        "--mode",
        choices=["auto", "sglang", "ollama"],
        default="auto",
        help="Что останавливать (default: auto - по config.serving.mode)",
    )
    args = parser.parse_args()

    cfg = load_serving_config(args.config)
    cwd = Path.cwd()

    targets: list[tuple[str, Path]] = []
    if args.mode in ("auto", "sglang"):
        targets.append(("sglang", cwd / cfg.sglang.pid_file))
    if args.mode in ("auto", "ollama") and cfg.mode == "ollama":
        targets.append(("ollama", cwd / cfg.ollama.pid_file))

    any_stopped = False
    for name, pid_file in targets:
        if name == "sglang":
            ok = stop_sglang(pid_file)
        else:
            ok = stop_ollama(pid_file)
        any_stopped = any_stopped or ok

    return 0 if any_stopped else 1


if __name__ == "__main__":
    sys.exit(main())
