from __future__ import annotations

import sys
import asyncio
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_SCRIPT_DIR = Path(__file__).resolve().parent
_WORKSPACE = Path(__file__).resolve().parents[3]
_PROJECT_ROOT = Path(__file__).resolve().parents[4]

for p in (_SCRIPT_DIR, _WORKSPACE, _PROJECT_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

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

from ior_reports import run_ior_report, build_dynamic_sql_from_prompt

async def test_drp_query():
    print("=== Testing Query 1: DRP-10050 ===")
    prompt = "Выведи ИОРы по DRP-10050"
    sql = build_dynamic_sql_from_prompt(prompt)
    print("Generated SQL:", sql)
    assert "DRP-10050" in sql, f"DRP code missing from SQL: {sql}"

    res = await run_ior_report(preset_name="ior_hypothesis", session_id="test_drp", user_prompt=prompt)
    print("\n--- Output Preview (first 1000 chars) ---")
    print(res[:1000])
    assert any(m in res for m in ("### 3.", "3. Рекомендации", "Гипотеза")), f"Hypotheses section missing! Response:\n{res[:500]}"
    assert "EVE-10050 не найден" not in res, "False EVE error found in response!"
    print("[OK] DRP-10050 test PASSED!\n")

async def test_march_moscow_bank_query():
    print("=== Testing Query 2: Иоры за март 2025 по московскому банку ===")
    prompt = "Иоры за март 2025 по московскому банку"
    sql = build_dynamic_sql_from_prompt(prompt)
    print("Generated SQL:", sql)
    assert "incdnt_entry_dt >= '2025-03-01'" in sql, f"Date filter missing: {sql}"
    assert "incdnt_entry_dt < '2025-04-01'" in sql, f"Date end filter missing: {sql}"
    assert "МОСКОВСКИЙ" in sql, f"Moscow Bank filter missing: {sql}"

    res = await run_ior_report(preset_name="ior_period_pao_sberbank", session_id="test_mb", user_prompt=prompt)
    print("\n--- Output Preview (first 1000 chars) ---")
    print(res[:1000])
    assert any(m in res for m in ("### 3.", "3. Рекомендации", "Гипотеза")), f"Hypotheses section missing! Response:\n{res[:500]}"
    print("[OK] March 2025 Moscow Bank test PASSED!\n")

async def main():
    await test_drp_query()
    await test_march_moscow_bank_query()
    print("ALL USER QUERY TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    asyncio.run(main())
