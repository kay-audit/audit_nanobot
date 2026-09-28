"""
cli.py — CLI адаптер для вызова скилла ior-analyzer через инструмент exec (bash skills/ior-analyzer/ior_analyze.sh).
"""
import sys
import os
import argparse
import asyncio
from importlib import import_module
from pathlib import Path

# Гарантируем UTF-8 вывод
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_FILE_PATH = Path(__file__).resolve()
_SKILL_DIR = _FILE_PATH.parent
while _SKILL_DIR.parent != _SKILL_DIR:
    if (_SKILL_DIR / "SKILL.md").exists() or _SKILL_DIR.name == "ior-analyzer":
        break
    _SKILL_DIR = _SKILL_DIR.parent

_SCRIPTS_DIR = _SKILL_DIR / "scripts"
_UTILS_DIR = _SKILL_DIR / "utils"

# Standalone CLI must use the same shared workspace/utils/db.py as gateway.
# Import that package before placing skill paths at the front of sys.path,
# then expose IOR-only submodules (utils.data_store, utils.resolve, ...).
_WORKSPACE_DIR = _PROJECT_ROOT / "workspace"
_workspace_path = str(_WORKSPACE_DIR)
if _workspace_path in sys.path:
    sys.path.remove(_workspace_path)
sys.path.insert(0, _workspace_path)
_shared_utils = import_module("utils")
_utils_path = str(_UTILS_DIR)
if _utils_path not in _shared_utils.__path__:
    _shared_utils.__path__.append(_utils_path)

for _dir in (_SKILL_DIR, _SCRIPTS_DIR):
    _sdir = str(_dir)
    if _sdir not in sys.path:
        sys.path.insert(0, _sdir)

from ior_reports import run_ior_report


def main():
    parser = argparse.ArgumentParser(description="CLI runner for ior-analyzer skill")
    parser.add_argument("--preset", default=None, help="Необязательное имя пресета; для ad-hoc запросов лучше не указывать")
    parser.add_argument("--prompt", default="", help="Текст запроса пользователя или JSON структурированного анализа")
    parser.add_argument("--session-id", default="cli_default_session", help="Идентификатор сессии")
    args = parser.parse_args()

    preset_map = {
        "удаленные иор": "deleted_ior",
        "удаленные инциденты": "deleted_ior",
        "финансовые последствия": "financial_consequences_ior",
        "возмещения": "vozmeshenie_ior",
        "нефинансовые последствия": "ior_nonfinancial_consequences",
        "кредиты без возможности взыскания": "credit_no_way_collect_debt"
    }

    preset = preset_map.get(args.preset.lower(), args.preset) if args.preset else None
    from analysis_mode.models import AnalysisRequestError
    try:
        result = asyncio.run(run_ior_report(preset_name=preset, session_id=args.session_id, user_prompt=args.prompt))
    except AnalysisRequestError as exc:
        parser.error(str(exc))
    print(result)


if __name__ == "__main__":
    main()
