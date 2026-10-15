"""
run_batch_test.py — Скрипт для полного smoke-прогона активных пресетов и Smart SQL.
Запускается через:
  python skills/ior-analyzer/scripts/run_batch_test.py
или через bash:
  bash skills/ior-analyzer/run_batch_test.sh
"""
import sys
import os
import asyncio
from pathlib import Path

# Гарантируем UTF-8 вывод в консоль
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

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

from ior_reports import run_ior_report

TEST_QUERIES = [
    {
        "id": 1,
        "preset": "ior_hypothesis",
        "kind": "preset",
        "prompt": "Сформируй общую аналитику и гипотезы по ИОР за март 2025 года"
    },
    {
        "id": 2,
        "preset": "deleted_ior",
        "kind": "preset",
        "prompt": "Удаленные ИОРы за март 2025 по ЮЗБ"
    },
    {
        "id": 3,
        "preset": "ior_nonfinancial_consequences",
        "kind": "preset",
        "prompt": "Качественные нефинансовые последствия за Q1 2025"
    },
    {
        "id": 4,
        "preset": "financial_consequences_ior",
        "kind": "preset",
        "prompt": "Детализация финансовых потерь за Q2 2025"
    },
    {
        "id": 5,
        "preset": "vozmeshenie_ior",
        "kind": "preset",
        "prompt": "Выведи возмещения по инцидентам за 2025 год"
    },
    {
        "id": 6,
        "preset": "report_period_specific_ior",
        "kind": "preset",
        "prompt": "Анализ карточки инцидента EVE-5008354"
    },
    {
        "id": 7,
        "preset": "ior_period_pao_sberbank",
        "kind": "preset",
        "prompt": "Сводный отчет по ИОРам ПАО Сбербанк за 2025 год"
    },
    {
        "id": 8,
        "preset": None,
        "kind": "smart_sql",
        "prompt": "Выведи утверждённые ИОР по процессу Эквайринг в Московском банке за Q1 2025"
    },
    {
        "id": 9,
        "preset": None,
        "kind": "smart_sql",
        "prompt": "Найди ИОР по цифровому профилю риска DRP-10121 за 2025 год"
    },
    {
        "id": 10,
        "preset": None,
        "kind": "smart_sql",
        "prompt": "Покажи финансовые последствия свыше 1 млн рублей по Юго-Западному банку за Q2 2025"
    },
]


async def run_batch():
    output_lines = []
    preset_count = sum(q["kind"] == "preset" for q in TEST_QUERIES)
    smart_sql_count = sum(q["kind"] == "smart_sql" for q in TEST_QUERIES)
    header = (
        "=" * 80
        + f"\n🚀 ЗАПУСК ПАКЕТНОГО ТЕСТИРОВАНИЯ ИОР-АНАЛИЗАТОРА "
          f"({len(TEST_QUERIES)} ЗАПРОСОВ: {preset_count} ПРЕСЕТОВ + {smart_sql_count} SMART SQL)\n"
        + "=" * 80
    )
    print(header)
    output_lines.append(header)

    for q in TEST_QUERIES:
        q_id = q["id"]
        preset = q["preset"]
        kind = q["kind"]
        prompt = q["prompt"]
        sess_id = f"batch_test_sess_{q_id}"

        mode_label = "SMART SQL / AUTO ROUTING" if kind == "smart_sql" else "ЯВНЫЙ ПРЕСЕТ"
        title = (
            f"\n\n{'='*80}\n📌 ТЕСТ #{q_id} | {mode_label} | "
            f"Preset: '{preset}' | Prompt: \"{prompt}\"\n{'='*80}\n"
        )
        print(title)
        output_lines.append(title)

        try:
            res = await run_ior_report(
                preset_name=preset,
                session_id=sess_id,
                user_prompt=prompt
            )
            print(res)
            output_lines.append(res)
        except Exception as err:
            err_msg = f"❌ Ошибка при исполнении теста #{q_id}: {err}"
            print(err_msg)
            output_lines.append(err_msg)

    footer = f"\n\n{'='*80}\n✅ ПАКЕТНОЕ ТЕСТИРОВАНИЕ ЗАВЕРШЕНО\n{'='*80}\n"
    print(footer)
    output_lines.append(footer)

    out_file = _SKILL_DIR / "batch_test_results.txt"
    try:
        with open(out_file, "w", encoding="utf-8") as f:
            f.write("\n".join(output_lines))
        print(f"📄 Все результаты пакетного тестирования сохранены в файл: {out_file}")
    except Exception as e:
        print(f"⚠️ Не удалось сохранить лог результатов: {e}")


def main():
    asyncio.run(run_batch())


if __name__ == "__main__":
    main()
