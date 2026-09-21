import sys
from pathlib import Path

_FILE_PATH = Path(__file__).resolve()
_SKILL_DIR = _FILE_PATH.parent
while _SKILL_DIR.parent != _SKILL_DIR:
    if (_SKILL_DIR / "SKILL.md").exists() or _SKILL_DIR.name == "ior-analyzer":
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

import sys
import importlib.util
from pathlib import Path

_SKILL_DIR = Path(__file__).resolve().parent
_SCRIPTS = _SKILL_DIR / "scripts"
_UTILS = _SKILL_DIR / "utils"

for p in (_SKILL_DIR, _SCRIPTS, _UTILS):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)

_impl_path = _SCRIPTS / "tool.py"
_spec = importlib.util.spec_from_file_location("ior_tool_impl", _impl_path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

IORAnalyzerTool = _mod.IORAnalyzerTool
Tool = IORAnalyzerTool
