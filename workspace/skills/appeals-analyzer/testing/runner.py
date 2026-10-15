from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Callable

# Точка входа запускается и как модуль пакета, и как самостоятельный скрипт
# (``python workspace/skills/appeals-analyzer/testing/runner.py`` — так его
# вызывает smoke-стадия audit_bridge). Во втором случае sys.path[0] указывает
# на каталог testing/, корня репозитория там нет, а ``__package__`` пуст, и
# относительный импорт падает с «attempted relative import with no known
# parent package». Поэтому при запуске скриптом поднимаем корень репозитория
# в sys.path и подгружаем соседние модули по файлу.
if __package__ in (None, ""):  # pragma: no cover - зависит от способа запуска
    import sys as _sys

    _HERE = Path(__file__).resolve().parent
    # _HERE = <repo>/workspace/skills/appeals-analyzer/testing, поэтому
    # parents[3] — корень репозитория (parents[0..2] — appeals-analyzer,
    # skills, workspace).
    _REPO_ROOT = _HERE.parents[3]
    if str(_REPO_ROOT) not in _sys.path:
        _sys.path.insert(0, str(_REPO_ROOT))

from lib.services.llm_client import call_llm_json
from workspace.utils.session_key import safe_session_key

if __package__ in (None, ""):  # pragma: no cover - зависит от способа запуска
    import importlib.util as _util
    import sys as _sys

    def _load_sibling(name: str):
        spec = _util.spec_from_file_location(
            f"_appeals_testing_{name}", _HERE / f"{name}.py"
        )
        module = _util.module_from_spec(spec)
        _sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    _data_generator = _load_sibling("data_generator")
    load_records = _data_generator.load_records
    TEST_SCHEMA = _data_generator.TEST_SCHEMA
    _llm_search = _load_sibling("llm_search")
    search, shortlist = _llm_search.search, _llm_search.shortlist
    render_report = _load_sibling("report").render_report
else:
    from .data_generator import TEST_SCHEMA, load_records
    from .llm_search import search, shortlist
    from .report import render_report

SESSION_ROOT = Path(__file__).resolve().parents[3] / "data_store" / "cache" / "testing" / "appeals" / "sessions"


def _parse_request(prompt: str) -> tuple[dict, str]:
    try:
        fields = next(csv.reader([prompt], skipinitialspace=True))
    except csv.Error:
        fields = []
    if len(fields) == 4:
        filters = {key: value.strip() for key, value in zip(("prd", "s_prd", "chnl"), fields[:3]) if value.strip()}
        return filters, fields[3].strip()
    return {}, prompt.strip()


def apply_filters(records: list[dict], filters: dict) -> list[dict]:
    result = records
    for key in ("prd", "s_prd", "chnl"):
        value = str(filters.get(key, "")).strip().lower()
        if value:
            accepted = {part.strip() for part in value.split(",") if part.strip()}
            result = [row for row in result if row[key].lower() in accepted]
    low, high = filters.get("date_from"), filters.get("date_to")
    if low:
        result = [row for row in result if row["date"] >= low]
    if high:
        result = [row for row in result if row["date"] <= high]
    return result


def _session_path(session_id: str) -> Path:
    return SESSION_ROOT / f"{safe_session_key(session_id)}.json"


def _write_session(session_id: str, query: str, ids: list[str]) -> None:
    path = _session_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"query": query, "selected_ids": ids}, ensure_ascii=False), encoding="utf-8")


async def run_testing_report(*, session_id: str, user_prompt: str, llm: Callable = call_llm_json) -> str:
    records = load_records()
    requested_id = re.search(r"APPEAL-TEST-\d{3,4}", user_prompt, re.I)
    session_path = _session_path(session_id)
    if requested_id and session_path.is_file():
        state = json.loads(session_path.read_text(encoding="utf-8"))
        wanted = requested_id.group(0).upper()
        if wanted in state.get("selected_ids", []):
            row = next((item for item in records if item["appeal_id"] == wanted), None)
            if row:
                return f"# {wanted}\n\n{row['date']} · {row['prd']} / {row['s_prd']} · {row['chnl']}\n\n{row['text']}"
    filters, query = _parse_request(user_prompt)
    candidates = apply_filters(records, filters)
    ranked = search(query, shortlist(query, candidates), llm)
    by_id = {row["appeal_id"]: row for row in candidates}
    selected = [{**by_id[item["id"]], "relevance_score": item["score"], "reason": item["reason"]} for item in ranked if item["id"] in by_id]
    _write_session(session_id, query, [row["appeal_id"] for row in selected])
    return render_report(query, selected)


MIN_RECORDS = 1000


def self_check() -> int:
    """Smoke-проверка стенда: данные читаются из test_d3 и их достаточно.

    Именно это объявлено в feature.yaml (testing.runner.smoke_expected:
    source_schema=test_d3, min_records=1000). Проверка не зовёт LLM — иначе
    smoke-stage audit_bridge зависел бы от наличия ключа провайдера.
    """
    records = load_records()
    print(f"source_schema={TEST_SCHEMA}")
    print(f"records={len(records)}")
    if not records:
        print(f"ОШИБКА: схема {TEST_SCHEMA} пуста — прогоните data_generator.py")
        return 1
    if len(records) < MIN_RECORDS:
        print(f"ОШИБКА: записей {len(records)} < требуемых {MIN_RECORDS}")
        return 1
    sample = records[0]
    missing = [key for key in ("appeal_id", "date", "prd", "s_prd", "chnl", "text")
               if not sample.get(key)]
    if missing:
        print(f"ОШИБКА: в первой записи нет полей: {missing}")
        return 1
    print(f"ПРОВЕРКА ПРОЙДЕНА: {len(records)} обращений из {TEST_SCHEMA}")
    return 0


if __name__ == "__main__":
    raise SystemExit(self_check())
