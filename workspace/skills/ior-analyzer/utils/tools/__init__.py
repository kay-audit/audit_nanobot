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

"""
Tools package. Импорт этого модуля авто-регистрирует все tool в REGISTRY
через side-effect (каждый модуль вызывает REGISTRY.register() при импорте).

Использование:
    from utils.tools.registry import REGISTRY
    catalog = REGISTRY.llm_catalog_compact()
"""
from utils.tools.registry import REGISTRY

# Side-effect: импорт модулей регистрирует tools. Optional production
# dependencies (PyYAML/DuckDB) не должны ломать импорт изолированного skill в
# offline regression tests; доступные модули всё равно регистрируются.
for _module_name in ("run_preset", "dataframe_ops", "introspect", "query_spec_tool"):
    try:
        __import__(f"utils.tools.{_module_name}")
    except ImportError:
        continue

__all__ = ["REGISTRY"]
