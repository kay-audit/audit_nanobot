import sys
from pathlib import Path

from workspace.tools.ior_analyzer import IORAnalyzerTool

SKILL_ROOT = Path(__file__).resolve().parent
IORAnalyzerTool._prepare_skill_utils_namespace(SKILL_ROOT)
for directory in (SKILL_ROOT, SKILL_ROOT / "scripts", SKILL_ROOT / "utils"):
    sys.path.insert(0, str(directory))
