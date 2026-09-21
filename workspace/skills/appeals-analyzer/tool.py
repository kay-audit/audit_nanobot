import sys
import importlib.util
from pathlib import Path

_FILE_PATH = Path(__file__).resolve()
_SKILL_DIR = _FILE_PATH.parent
while _SKILL_DIR.parent != _SKILL_DIR:
    if (_SKILL_DIR / "SKILL.md").exists() or _SKILL_DIR.name == "appeals-analyzer":
        break
    _SKILL_DIR = _SKILL_DIR.parent

_SCRIPTS_DIR = _SKILL_DIR / "scripts"
_UTILS_DIR = _SKILL_DIR / "utils"

for _dir in (_SKILL_DIR, _SCRIPTS_DIR, _UTILS_DIR):
    _sdir = str(_dir)
    if _sdir not in sys.path:
        sys.path.insert(0, _sdir)

# -*- coding: utf-8 -*-
"""
Tool registration shim for nanobot.

nanobot automatic skill loader inspects workspace/skills/<skill_dir>/tool.py
and registers the Tool class.
"""

_impl_path = _SCRIPTS_DIR / "tool.py"
_spec = importlib.util.spec_from_file_location("appeals_tool_impl", _impl_path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

AppealsAnalyzerTool = _mod.AppealsAnalyzerTool
Tool = AppealsAnalyzerTool
