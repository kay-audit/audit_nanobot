"""JSON stdin/stdout interface for the nanobot tool."""
from __future__ import annotations

import contextlib
import json
import os
import re
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(payload: dict) -> dict:
    if sys.version_info[:2] != (3, 12):
        raise ValueError("Требуется Python 3.12.")
    if not isinstance(payload, dict) or set(payload) - {"action", "question", "key", "dry_run"}:
        raise ValueError("Некорректные аргументы.")
    action = payload.get("action", "query")
    question = payload.get("question", "")
    key = payload.get("key", "")
    dry_run = payload.get("dry_run", False)
    if not isinstance(question, str) or not isinstance(key, str) or not isinstance(dry_run, bool):
        raise ValueError("Неверные типы аргументов.")
    if action not in {"query", "status", "prepare", "load"}:
        raise ValueError("Неизвестное действие.")
    if action == "query" and not question.strip():
        raise ValueError("Укажите вопрос.")
    if action == "load" and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:-[0-9]+)?", key):
        raise ValueError("Укажите ключ задачи или проекта.")
    sys.path.insert(0, str(ROOT))
    from wiki_agent import WikiAgent
    agent = WikiAgent(ROOT)
    if action == "status":
        result = asdict(agent.doctor())
    elif action == "query":
        result = asdict(agent.jira.query(question, dry_run=dry_run))
    elif action == "prepare":
        result = asdict(agent.jira.prepare())
    else:
        result = asdict(agent.jira.load(key.upper()))
    return {"status": "ok", "action": action, "result": result}


def main() -> int:
    try:
        with contextlib.redirect_stdout(sys.stderr):
            payload = run(json.loads(sys.stdin.read()))
        code = 0
    except Exception as exc:
        payload = {"status": "error", "error_type": type(exc).__name__, "message": str(exc)}
        code = 2
    output = json.dumps(payload, ensure_ascii=False, default=str)
    key = os.environ.get("MINIMAX_API_KEY")
    if key:
        output = output.replace(key, "[REDACTED]")
    print(output)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
