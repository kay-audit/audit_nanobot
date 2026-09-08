"""scripts.serving.health - CLI для одноразовой проверки LLM-сервера.

Использование:
    python -m scripts.serving.health --api-base http://localhost:30000/v1
    python -m scripts.serving.health --config config.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import load_serving_config
from .health_check import probe


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверить готовность LLM-сервера")
    parser.add_argument("--api-base", default=None, help="Базовый URL (например http://localhost:30000/v1)")
    parser.add_argument("--api-key", default="", help="Bearer-токен (если требуется)")
    parser.add_argument("--config", type=Path, default=None, help="Путь к config.json (для дефолтов)")
    args = parser.parse_args()

    api_base = args.api_base
    api_key = args.api_key
    if not api_base:
        cfg = load_serving_config(args.config)
        if cfg.mode == "sglang":
            api_base = cfg.api_base or f"http://localhost:{cfg.sglang.port}/v1"
            api_key = api_key or cfg.api_key or cfg.sglang.api_key
        elif cfg.mode == "ollama":
            api_base = cfg.api_base or f"http://{cfg.ollama.host}:{cfg.ollama.port}/v1"
            api_key = api_key or cfg.api_key or cfg.ollama.api_key
        else:
            print("error: --api-base is required (no serving config)", file=sys.stderr)
            return 2

    if not api_base:
        print("error: empty api_base", file=sys.stderr)
        return 2

    result = probe(api_base, api_key=api_key)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
