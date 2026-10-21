"""History CLI: default internal LLM pipeline; --raw is parser-only debugging."""
from __future__ import annotations

import argparse
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parents[3]
for path in (ROOT, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", default=[])
    parser.add_argument("--target")
    parser.add_argument("--overview")
    parser.add_argument("--raw", action="store_true", help="Parser only, no LLM or artifacts")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--field", choices=("changes", "current"), default="changes")
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--status", metavar="OPERATION_ID")
    parser.add_argument("--resume", metavar="OPERATION_ID")
    parser.add_argument("--run-operation", help=argparse.SUPPRESS)
    parser.add_argument("--workspace-root", default=str(ROOT))
    parser.add_argument("--session-key")
    parser.add_argument("--profile", default="test")
    args = parser.parse_args(argv)
    code = 0
    try:
        # Configuration imports and all runtime diagnostics stay off stdout.
        with redirect_stdout(sys.stderr):
            from application.history import analyze
            from application.history_pipeline import (
                launch,
                run_operation,
                session_key,
                start,
                status,
            )
            if not args.raw:
                import config
                if hasattr(config, "is_settings_initialized") and not config.is_settings_initialized():
                    config._initialize_settings(args.profile)
            if sum(bool(v) for v in (args.raw, args.status, args.resume, args.run_operation, args.background)) > 1:
                raise ValueError("Choose one of --raw, --status, --run-operation, --background")
            if not args.raw and (args.offset != 0 or args.limit != 5 or args.field != "changes"):
                raise ValueError("Pagination is available only with --raw")
            if not args.input and not (args.status or args.resume or args.run_operation):
                raise ValueError("At least one --input is required")
            root = Path(args.workspace_root).resolve()
            if args.raw:
                result = analyze(args.input, target=args.target, overview=args.overview,
                                 offset=args.offset, limit=args.limit, field=args.field)
            elif args.status or args.resume or args.run_operation:
                if not args.session_key:
                    raise ValueError("Supply explicit --session-key")
                session = session_key(args.input, args.session_key)
                if args.resume:
                    result = launch(args.resume, root, session, profile=args.profile)
                else:
                    function = status if args.status else run_operation
                    result = function(args.status or args.run_operation, root, session)
            else:
                result = start(args.input, target=args.target, overview=args.overview,
                               root=root, session=args.session_key, background=args.background, profile=args.profile)
    except Exception as exc:
        # Do not include server response bodies or credentials in the agent output.
        detail = str(exc) if isinstance(exc, (ValueError, FileNotFoundError)) else type(exc).__name__
        result, code = {"status": "error", "mode": "doc_history", "message": detail}, 1
        if args.run_operation and args.session_key:
            try:
                from cache.history import directory, write
                write(directory(Path(args.workspace_root).resolve(), args.session_key, args.run_operation) / "state.json", result)
            except Exception:
                pass
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
