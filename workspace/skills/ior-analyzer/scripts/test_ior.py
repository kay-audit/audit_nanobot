"""
test_ior.py — Тестирование скилла ior-analyzer (Парето, 3σ, графики, сессии, JOIN-пресеты БЗ, сложные ad-hoc запросы).
"""
import sys
from pathlib import Path


import sys
from pathlib import Path
import asyncio
import pandas as pd
import numpy as np

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

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

from ior_profiler import profile_dataframe, get_total_and_direct_loss
from ior_hypothesis import generate_hypothesis_narrative, generate_dynamics_chart
from ior_reports import run_ior_report, build_dynamic_sql_from_prompt, IOR_FULL_SQL_QUERIES
import sys
from pathlib import Path


from utils.session_extract_manager import get_session_extract, clear_session_extract


def test_profiling_and_pareto():
    np.random.seed(42)
    n = 100
    df = pd.DataFrame({
        "incdnt_sum": np.random.exponential(scale=1000, size=n),
        "incdnt_drct_dmg_sum": np.random.exponential(scale=500, size=n),
        "date": pd.date_range("2025-01-01", periods=n, freq="D")
    })
    df.loc[0, "incdnt_sum"] = 500000.0
    df.loc[1, "incdnt_sum"] = 300000.0

    total_loss, direct_loss = get_total_and_direct_loss(df)
    assert total_loss > 800000.0

    profile = profile_dataframe(df)
    assert "Правило Парето" in profile
    assert "Аномальные выбросы (3σ" in profile
    print("[OK] test_profiling_and_pareto PASSED")


def test_chart_generation():
    df = pd.DataFrame({
        "incdnt_sum": [100, 200, 300, 400, 500, 600],
        "date": ["2025-01-10", "2025-01-20", "2025-02-10", "2025-02-20", "2025-03-10", "2025-03-20"]
    })
    chart_path = generate_dynamics_chart(df, "test_session_1")
    assert chart_path is not None
    assert "chart_ior_" in chart_path
    print("[OK] test_chart_generation PASSED")


def test_knowledge_base_preset_queries():
    assert "JOIN" in IOR_FULL_SQL_QUERIES["financial_consequences_ior"]
    assert "JOIN" in IOR_FULL_SQL_QUERIES["vozmeshenie_ior"]
    assert "JOIN" in IOR_FULL_SQL_QUERIES["credit_no_way_collect_debt"]
    assert "JOIN" in IOR_FULL_SQL_QUERIES["ior_nonfinancial_consequences"]
    assert "УДАЛЁН" in IOR_FULL_SQL_QUERIES["deleted_ior"]
    print("[OK] test_knowledge_base_preset_queries PASSED")


def test_complex_adhoc_query_builder():
    prompt = "Покажи мне инциденты по ТБ Среднерусский с суммой потерь более 1000000 рублей за 1 квартал 2025 года"
    sql = build_dynamic_sql_from_prompt(prompt)
    assert "incdnt_entry_dt >=" in sql
    assert "2025-01-01" in sql
    assert "incdnt_sum >=" in sql
    assert "СРЕДНЕРУССКИЙ" in sql
    print("[OK] test_complex_adhoc_query_builder PASSED")


async def test_ior_reports_session_single_extract():
    clear_session_extract("test_session_99")

    res1 = await run_ior_report("financial_consequences_ior", "test_session_99")
    extract1 = get_session_extract("test_session_99")
    assert extract1 is not None

    res2 = await run_ior_report(None, "test_session_99")
    extract2 = get_session_extract("test_session_99")

    assert extract1["df"] is extract2["df"]
    print("[OK] test_ior_reports_session_single_extract PASSED")


def test_specific_code_routing():
    from ior_reports import has_specific_codes, detect_preset_from_prompt

    prompt_drp = "ИОРы по DRP-10274"
    assert has_specific_codes(prompt_drp) is True
    assert detect_preset_from_prompt(prompt_drp) is None

    sql_drp = build_dynamic_sql_from_prompt(prompt_drp)
    assert "risk_profile_id" in sql_drp
    assert "DRP-10274" in sql_drp

    prompt_eve = "Информация по событию EVE-1234567"
    assert has_specific_codes(prompt_eve) is True
    sql_eve = build_dynamic_sql_from_prompt(prompt_eve)
    assert "incdnt_sid" in sql_eve
    assert "EVE-1234567" in sql_eve

    prompt_sbr = "ИОР по блоку SBR-5432"
    assert has_specific_codes(prompt_sbr) is True
    sql_sbr = build_dynamic_sql_from_prompt(prompt_sbr)
    assert "funct_block_id" in sql_sbr
    assert "SBR-5432" in sql_sbr

    prompt_proc = "Процессы по П2608 за 2025 год"
    assert has_specific_codes(prompt_proc) is True
    sql_proc = build_dynamic_sql_from_prompt(prompt_proc)
    assert "process_lvl" in sql_proc
    assert "П2608" in sql_proc
    print("[OK] test_specific_code_routing PASSED")


def test_excel_column_reordering():
    from utils.dataframe_ops import prepare_df_for_excel
    df = pd.DataFrame({
        "org_struct_lvl_3_name": ["Московский банк"],
        "incdnt_summary_descr_txt": ["Сбой в банкомате"],
        "incdnt_id": [101],
        "incdnt_sum": [50000.0]
    })
    df_out = prepare_df_for_excel(df)
    cols = list(df_out.columns)
    assert cols[0] in ("incdnt_id", "Идентификационный ключ инцидента операционного риска"), f"Expected incdnt_id first, got: {cols}"
    descr_idx = next(i for i, c in enumerate(cols) if any(k in str(c) for k in ("incdnt_summary_descr_txt", "Предварительное описание")))
    org_idx = next(i for i, c in enumerate(cols) if any(k in str(c) for k in ("org_struct_lvl_3_name", "Орг. структура")))
    assert descr_idx < org_idx
    print("[OK] test_excel_column_reordering PASSED")


async def test_small_dataset_summarization_only():
    df_small = pd.DataFrame({
        "incdnt_id": list(range(1, 10)), # 9 IORs (< 20)
        "incdnt_sid": [f"EVE-00000{i}" for i in range(1, 10)],
        "incdnt_status_name": ["Утвержден"] * 9,
        "incdnt_sum": [1000.0 * i for i in range(1, 10)],
        "incdnt_entry_dt": ["2025-01-15"] * 9
    })
    narrative = await generate_hypothesis_narrative("Запрос малой выборки", df_small, "test_small_session")
    assert "Гипотеза 1" not in narrative, f"Hypothesis generated for < 20 IORs!\n{narrative}"
    assert "### 4. Аналитические гипотезы" not in narrative and "### 3. Рекомендации и гипотезы" not in narrative
    print("[OK] test_small_dataset_summarization_only PASSED")


def test_auditor_15_queries():
    from ior_reports import build_dynamic_sql_from_prompt, detect_preset_from_prompt
    from utils.resolve.period_parser import parse_period

    # 1. Выведи ИОРы за Q1 2025 года по московскому банку
    q1 = "Выведи ИОРы за Q1 2025 года по московскому банку"
    p1 = parse_period(q1)
    sql1 = build_dynamic_sql_from_prompt(q1)
    assert p1 and p1.start == "2025-01-01" and p1.end == "2025-04-01"
    assert "МОСКОВСКИЙ" in sql1

    # 2. Возмещения по ИОРам за март 2025 по ВВБ
    q2 = "Возмещения по ИОРам за март 2025 по ВВБ"
    p2 = parse_period(q2)
    sql2 = build_dynamic_sql_from_prompt(q2)
    assert p2 and p2.start == "2025-03-01" and p2.end == "2025-04-01"
    assert "ВОЛГО-ВЯТСКИЙ" in sql2
    assert "incident_recovery" in sql2 or "recovery_rub_amt" in sql2

    # 3. Нефинансовые последствия за Q1 и Q2 2025 года
    q3 = "Нефинансовые последствия за Q1 и Q2 2025 года"
    p3 = parse_period(q3)
    sql3 = build_dynamic_sql_from_prompt(q3)
    print("DEBUG SQL3:", sql3)
    assert p3 and p3.start == "2025-01-01" and p3.end == "2025-07-01", f"Got p3: {p3}"
    assert "incident_nonfin_impact" in sql3 or "nonfin_impact" in sql3

    # 4. Финансовые последствия по ИОР за апрель 2026 по ЮЗБ
    q4 = "Финансовые последствия по ИОР за апрель 2026 по ЮЗБ"
    p4 = parse_period(q4)
    sql4 = build_dynamic_sql_from_prompt(q4)
    assert p4 and p4.start == "2026-04-01" and p4.end == "2026-05-01"
    assert "ЮГО-ЗАПАДНЫЙ" in sql4
    assert "incident_fin_impact" in sql4 or "fin_impact_rub_amt" in sql4

    # 5. Удаленные ИОРы за 01.04.2025 по 01.07.2025 по блоку риски
    q5 = "Удаленные ИОРы за 01.04.2025 по 01.07.2025 по блоку риски"
    p5 = parse_period(q5)
    sql5 = build_dynamic_sql_from_prompt(q5)
    assert p5 and p5.start == "2025-04-01" and p5.end == "2025-07-02"
    assert "УДАЛЁН" in sql5 or "incident_stts_chng" in sql5
    assert "РИСК" in sql5

    # 6. ИОРы со статусом утвержден за Q1 2025 по домклику
    q6 = "ИОРы со статусом утвержден за Q1 2025 по домклику"
    p6 = parse_period(q6)
    sql6 = build_dynamic_sql_from_prompt(q6)
    assert p6 and p6.start == "2025-01-01" and p6.end == "2025-04-01"
    assert "УТВЕРЖДЁН" in sql6
    assert "ДОМКЛИК" in sql6

    # 7. Динамика потерь за январь-апрель 2026
    q7 = "Динамика потерь за январь-апрель 2026"
    p7 = parse_period(q7)
    assert p7 and p7.start == "2026-01-01" and p7.end == "2026-05-01"

    # 8. Выведи всю информацию по EVE-1234567
    q8 = "Выведи всю информацию по EVE-1234567"
    sql8 = build_dynamic_sql_from_prompt(q8)
    assert "EVE-1234567" in sql8 and "incdnt_sid" in sql8

    # 9. Выведи ИОРы по DRP-10121
    q9 = "Выведи ИОРы по DRP-10121"
    sql9 = build_dynamic_sql_from_prompt(q9)
    assert "DRP-10121" in sql9 and "risk_profile_id" in sql9

    # 10. Выведи ИОРы по процессу П2608
    q10 = "Выведи ИОРы по процессу П2608"
    sql10 = build_dynamic_sql_from_prompt(q10)
    assert "П2608" in sql10 and "process_lvl" in sql10

    # 11. Выведи ИОРы по процессу департамент учета и отчетности
    q11 = "Выведи ИОРы по процессу департамент учета и отчетности"
    sql11 = build_dynamic_sql_from_prompt(q11)
    assert "УЧЕТ" in sql11 or "ОТЧЕТНОСТ" in sql11

    # 12. Выведи ИОРы за март 2025 по дивизиону риски розничного бизнеса где сумма потерь составлят больше 100000 рублей
    q12 = "Выведи ИОРы за март 2025 по дивизиону риски розничного бизнеса где сумма потерь составлят больше 100000 рублей"
    p12 = parse_period(q12)
    sql12 = build_dynamic_sql_from_prompt(q12)
    assert p12 and p12.start == "2025-03-01" and p12.end == "2025-04-01"
    assert "100000" in sql12
    assert "РИСК" in sql12

    # 13. Выведи ИОРы за 2025 год по эквайрингу
    q13 = "Выведи ИОРы за 2025 год по эквайрингу"
    p13 = parse_period(q13)
    sql13 = build_dynamic_sql_from_prompt(q13)
    assert p13 and p13.start == "2025-01-01" and p13.end == "2026-01-01"
    assert "ЭКВАЙРИНГ" in sql13

    # 14. Выведи ИОРы за Q2 2025 года по московскому банку по DRP-10147
    q14 = "Выведи ИОРы за Q2 2025 года по московскому банку по DRP-10147"
    p14 = parse_period(q14)
    sql14 = build_dynamic_sql_from_prompt(q14)
    assert p14 and p14.start == "2025-04-01" and p14.end == "2025-07-01"
    assert "МОСКОВСКИЙ" in sql14
    assert "DRP-10147" in sql14

    # 15. Выведи гипотезу по инцидентам за Q3 2025 по блоку B2C
    q15 = "Выведи гипотезу по инцидентам за Q3 2025 по блоку B2C"
    p15 = parse_period(q15)
    sql15 = build_dynamic_sql_from_prompt(q15)
    assert p15 and p15.start == "2025-07-01" and p15.end == "2025-10-01"
    assert "B2C" in sql15 or "РОЗНИЦ" in sql15

    print("[OK] test_auditor_15_queries PASSED")


if __name__ == "__main__":
    test_profiling_and_pareto()
    test_chart_generation()
    test_knowledge_base_preset_queries()
    test_complex_adhoc_query_builder()
    test_specific_code_routing()
    test_excel_column_reordering()
    test_auditor_15_queries()
    asyncio.run(test_ior_reports_session_single_extract())
    asyncio.run(test_small_dataset_summarization_only())
    print("\nALL IOR TESTS PASSED SUCCESSFULLY!")

