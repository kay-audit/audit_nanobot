"""Standalone CLI for appeals-analyzer without importing Nanobot runtime."""
from __future__ import annotations

import argparse
import asyncio
import importlib
import importlib.util
import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable

_SKILL_DIR = Path(__file__).resolve().parents[1]
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_WORKSPACE_DIR = _PROJECT_ROOT / "workspace"
_DEFAULT_LOG = _SKILL_DIR / "logs" / "appeals_analyzer_cli.log"

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")


def configure_logging(level: str = "INFO", log_file: Path | None = _DEFAULT_LOG) -> None:
    """Send identical stage logs to stderr and an optional rotating file."""
    numeric_level = getattr(logging, level.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError(f"Unknown log level: {level}")
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file is not None:
        log_file = log_file.expanduser().resolve()
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                log_file,
                maxBytes=10 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
        )
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(numeric_level)
    for handler in handlers:
        handler.setLevel(numeric_level)
        handler.setFormatter(formatter)
        root.addHandler(handler)


def load_shared_db():
    """Load the canonical workspace DB module and reject import collisions."""
    expected_utils = (_WORKSPACE_DIR / "utils").resolve()
    loaded_utils = sys.modules.get("utils")
    if loaded_utils is not None:
        loaded_paths = {
            Path(path).resolve()
            for path in getattr(loaded_utils, "__path__", ())
        }
        if expected_utils not in loaded_paths:
            raise RuntimeError(
                "Standalone appeals requires workspace/utils; "
                "another top-level utils package is already loaded."
            )

    for path in (_PROJECT_ROOT, _WORKSPACE_DIR):
        value = str(path)
        if value in sys.path:
            sys.path.remove(value)
        sys.path.insert(0, value)

    shared_utils = importlib.import_module("utils")
    loaded_paths = {
        Path(path).resolve()
        for path in getattr(shared_utils, "__path__", ())
    }
    if expected_utils not in loaded_paths:
        raise RuntimeError(
            "Standalone appeals requires workspace/utils; "
            "another top-level utils package is already loaded."
        )
    shared_db = importlib.import_module("utils.db")
    expected_db = expected_utils / "db.py"
    if Path(getattr(shared_db, "__file__", "")).resolve() != expected_db:
        raise RuntimeError("Standalone appeals loaded a non-workspace utils.db module.")
    return shared_db


def start_standalone_db_runtime(shared_db) -> None:
    """Configure and start the shared pool owned by this CLI process."""
    dsn = shared_db.resolve_dsn()
    if not dsn:
        raise RuntimeError(
            "Shared DB DSN is not configured in channels.postgres.dsn."
        )
    from config import SETTINGS

    postgres = SETTINGS.get("channels", {}).get("postgres", {})
    pool_config = postgres.get("pool", {}) if isinstance(postgres, dict) else {}
    shared_db.set_pool_config(pool_config)
    shared_db.configure(dsn)
    shared_db.start()


def load_standalone_runner() -> Callable:
    """Load the copied skill privately after pinning shared workspace imports."""
    load_shared_db()
    package_name = "appeals_analyzer_standalone"
    package_init = _SKILL_DIR / "__init__.py"
    loaded = sys.modules.get(package_name)
    if loaded is None:
        for module_name in list(sys.modules):
            if module_name.startswith(package_name + "."):
                sys.modules.pop(module_name, None)
        spec = importlib.util.spec_from_file_location(
            package_name,
            package_init,
            submodule_search_locations=[str(_SKILL_DIR)],
        )
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Cannot create standalone package from {package_init}")
        package = importlib.util.module_from_spec(spec)
        sys.modules[package_name] = package
        try:
            spec.loader.exec_module(package)
        except Exception:
            sys.modules.pop(package_name, None)
            raise
    else:
        origin = getattr(loaded, "__file__", None)
        if origin is None or Path(origin).resolve() != package_init.resolve():
            raise RuntimeError("Standalone appeals namespace is occupied by another package")
    reports = importlib.import_module(f"{package_name}.scripts.appeals_reports")
    return reports.run_appeals_report


def _read_prompt(args: argparse.Namespace, parser: argparse.ArgumentParser) -> str:
    sources = sum(bool(value) for value in (args.prompt, args.prompt_file, args.positional_prompt))
    if sources != 1:
        parser.error("Передайте prompt ровно одним способом: позиционно, через --prompt или --prompt-file")
    if args.prompt_file:
        prompt = args.prompt_file.read_text(encoding="utf-8").strip()
    elif args.prompt is not None:
        prompt = args.prompt.strip()
    else:
        prompt = " ".join(args.positional_prompt).strip()
    if not prompt:
        parser.error("Prompt не может быть пустым")
    return prompt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Standalone appeals-analyzer runner (corporate GigaChat, no vLLM by default)",
        epilog='Пример: ./appeals_analyze.sh \'"Кредиты", "", "IVR", "жалобы за 2026 год"\'',
    )
    parser.add_argument("positional_prompt", nargs="*", help="Structured four-field prompt")
    parser.add_argument("--prompt", help="Structured four-field prompt")
    parser.add_argument("--prompt-file", type=Path, help="UTF-8 file containing the prompt")
    parser.add_argument("--session-id", default="cli_default_session", help="Session identifier")
    parser.add_argument("--log-file", type=Path, default=_DEFAULT_LOG, help="Rotating log file")
    parser.add_argument("--console-only", action="store_true", help="Disable file logging")
    parser.add_argument("--log-level", default=os.getenv("APPEALS_LOG_LEVEL", "INFO"), choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    parser.add_argument("--allow-vllm", action="store_true", help="Allow the legacy localhost vLLM fallback")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    prompt = _read_prompt(args, parser)
    if args.allow_vllm:
        os.environ.pop("APPEALS_DISABLE_VLLM", None)
    else:
        os.environ["APPEALS_DISABLE_VLLM"] = "1"
    configure_logging(args.log_level, None if args.console_only else args.log_file)
    logger = logging.getLogger("appeals_analyzer.cli")
    started = time.monotonic()
    logger.info("Standalone run started: session_id=%s prompt_chars=%s vllm_enabled=%s", args.session_id, len(prompt), args.allow_vllm)
    logger.info("Skill directory: %s", _SKILL_DIR)
    logger.info("RAG cache override: %s", os.getenv("APPEALS_RAG_CACHE_DIR", "<workspace caches_pipelines/cache_le_finale2>"))
    shared_db = None
    db_started = False
    try:
        shared_db = load_shared_db()
        start_standalone_db_runtime(shared_db)
        db_started = True
        logger.info("Standalone shared DB pool started by appeals CLI")
        runner = load_standalone_runner()
        result = asyncio.run(runner(session_id=args.session_id, user_prompt=prompt))
    except KeyboardInterrupt:
        logger.warning("Standalone run interrupted by user")
        return 130
    except Exception:
        logger.exception("Standalone run failed")
        return 1
    finally:
        if db_started and shared_db is not None:
            shared_db.shutdown()
            logger.info("Standalone shared DB pool stopped")
    logger.info("Standalone run finished in %.2fs; result_chars=%s", time.monotonic() - started, len(str(result)))
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
