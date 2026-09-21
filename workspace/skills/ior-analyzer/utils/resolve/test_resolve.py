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
test_resolve.py — Тесты модуля resolve (разбор периодов и умное определение колонок фильтров).
"""
import sys
from pathlib import Path
import pandas as pd

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from period_parser import parse_period
from grounding import resolve_filter_column, apply_smart_filter


def test_period_parsing():
    p1 = parse_period("выгрузи за январь 2025 года")
    assert p1 is not None
    assert p1.start == "2025-01-01"
    assert p1.end == "2025-02-01"
    assert p1.kind == "month"

    p2 = parse_period("отчёт за 1 квартал 2025")
    assert p2 is not None
    assert p2.start == "2025-01-01"
    assert p2.end == "2025-04-01"
    assert p2.kind == "quarter"

    p3 = parse_period("данные за 2024 год")
    assert p3 is not None
    assert p3.start == "2024-01-01"
    assert p3.end == "2025-01-01"
    assert p3.kind == "year"

    print("[OK] test_period_parsing PASSED")


def test_smart_grounding():
    df = pd.DataFrame({
        "incdnt_sid": ["EVE-1", "EVE-2"],
        "org_struct_lvl_2_name": ["Московский банк", "Среднерусский банк"],
        "process_lvl_1_name": ["Кредитование", "Депозиты"]
    })

    col = resolve_filter_column(df, "Среднерусский", "тб")
    assert col == "org_struct_lvl_2_name"

    filtered_df, used_col = apply_smart_filter(df, "Среднерусский", "тб")
    assert len(filtered_df) == 1
    assert used_col == "org_struct_lvl_2_name"

    print("[OK] test_smart_grounding PASSED")


if __name__ == "__main__":
    test_period_parsing()
    test_smart_grounding()
    print("\nALL RESOLVE TESTS PASSED SUCCESSFULLY!")
