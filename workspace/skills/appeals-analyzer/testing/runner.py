from __future__ import annotations

import csv
import json
import re
from datetime import datetime
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


def _natural_filters(prompt: str) -> dict:
    low = prompt.casefold()
    result = {}
    year = re.search(r"\b(20\d{2})\s*год", low)
    quarter = re.search(r"(?:втор[а-яё]*|2)[ -]*(?:квартал[а-яё]*)\s+(20\d{2})", low)
    if quarter:
        result.update(date_from=quarter[1]+"-04-01", date_to=quarter[1]+"-06-30")
    elif year:
        result.update(date_from=year[1]+"-01-01", date_to=year[1]+"-12-31")
    if re.search(r"контакт[ -]центр", low): result["chnl"] = ["Контакт-центр"]
    elif re.search(r"мобильн[а-яё]*\s+банк", low): result["chnl"] = ["Мобильный банк"]
    elif re.search(r"\bapi\b", low): result["chnl"] = ["API"]
    if "ипотек" in low: result["prd"] = ["Ипотека"]
    return result


def _parse_request(prompt: str) -> tuple[dict, str]:
    if prompt.strip().startswith('Анализ обращений\n'):
        sections=prompt.strip().split('\n\n')
        filters={}
        labels={'Продукт:':'prd','Субпродукт:':'s_prd','Подпродукт:':'s_prd','Канал:':'chnl'}
        for index,section in enumerate(sections[1:],1):
            heading,_,body=section.partition('\n')
            if heading=='Запрос:':
                query='\n\n'.join([body,*sections[index+1:]]).strip()
                if not query:
                    raise ValueError('Canonical Appeals prompt is empty')
                return filters,query
            if heading in labels:
                filters[labels[heading]]=[line[2:].strip() for line in body.splitlines() if line.startswith('- ')]
            elif heading=='Период:':
                dates=[datetime.strptime(value,'%d.%m.%Y').date().isoformat() for value in re.findall(r'\d{2}\.\d{2}\.\d{4}',body)]
                if len(dates)==2:
                    filters.update(date_from=dates[0],date_to=dates[1])
                elif len(dates)==1:
                    filters['date_to' if body.strip().startswith('по ') else 'date_from']=dates[0]
                else:
                    raise ValueError('Invalid canonical Appeals period')
            else:
                raise ValueError('Unknown canonical Appeals section')
        raise ValueError('Canonical Appeals request has no prompt')
    try:
        payload = json.loads(prompt)
    except (ValueError, TypeError):
        payload = None
    if isinstance(payload, dict) and payload.get('request_type') == 'appeals_analysis':
        filters = payload.get('filters') or {}
        if not isinstance(filters, dict):
            raise ValueError('appeals_analysis.filters must be an object')
        return filters, str(payload.get('prompt') or '').strip()
    try:
        fields = next(csv.reader([prompt], skipinitialspace=True))
    except csv.Error:
        fields = []
    if len(fields) == 4:
        filters = {key: value.strip() for key, value in zip(("prd", "s_prd", "chnl"), fields[:3]) if value.strip()}
        return filters, fields[3].strip()
    return _natural_filters(prompt), prompt.strip()


def apply_filters(records: list[dict], filters: dict) -> list[dict]:
    result = records
    for key in ("prd", "s_prd", "chnl"):
        value = filters.get(key, '')
        value = ','.join(value) if isinstance(value, list) else str(value)
        value = value.strip().lower()
        if value:
            accepted = {part.strip() for part in value.split(",") if part.strip()}
            result = [row for row in result if row[key].lower() in accepted]
    ids = filters.get('app_row_id') or filters.get('ids')
    if ids:
        accepted_ids = {str(value) for value in (ids if isinstance(ids, list) else [ids])}
        result = [row for row in result if row['appeal_id'] in accepted_ids]
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
    path.write_text(json.dumps({"query": query, "selected_ids": ids, "final_ids": ids}, ensure_ascii=False), encoding="utf-8")


def _has_error_admission(text: str) -> bool:
    return bool(re.search(
        r"ошибк[а-яё]*\s+признан[а-яё]*|(?<!не )признал[аи]?\s+(?:свою\s+)?ошибк|(?<!не )подтвердил[аи]?\s+ошибк",
        text.casefold(),
    ))


async def run_testing_report(*, session_id: str, user_prompt: str, llm: Callable = call_llm_json) -> str:
    records = load_records()
    requested_id = re.search(r"(?:APPEAL-TEST-\d{3,4}|DEMO-SYNTHETIC-(?:RICH-)?\d{6})", user_prompt, re.I)
    session_path = _session_path(session_id)
    if requested_id:
        wanted = requested_id.group(0).upper()
        row = next((item for item in records if item["appeal_id"] == wanted), None)
        if row:
            return f"# {wanted}\n\n{row['date']} · {row['prd']} / {row['s_prd']} · {row['chnl']}\n\n{row['text']}"
        return f"Обращение {wanted} в тестовом источнике не найдено."
    if re.search(r"среди (?:найденных|них)|из предыдущ|оставь|из найденных", user_prompt, re.I):
        if not session_path.is_file():
            return "Нет сохранённой выборки обращений в этой беседе. Выполните исходный поиск."
        state = json.loads(session_path.read_text(encoding="utf-8"))
        allowed = set(state.get("final_ids", state.get("selected_ids", [])))
        records = [item for item in records if item["appeal_id"] in allowed]
    filters, query = _parse_request(user_prompt)
    candidates = apply_filters(records, filters)
    ranked = search(query, shortlist(query, candidates), llm)
    by_id = {row["appeal_id"]: row for row in candidates}
    selected = [{**by_id[item["id"]], "relevance_score": item["score"], "reason": item["reason"]} for item in ranked if item["id"] in by_id]
    # Исправление расхождения само по себе не доказывает признание ошибки.
    # Для такого follow-up требуем явную опору в исходной переписке/описании.
    if all(stem in query.casefold() for stem in ("оператор", "призн", "ошиб")):
        selected = [row for row in selected if _has_error_admission(row["text"])]
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


def export_selected(session_id: str) -> Path | None:
    from uuid import uuid4
    import pandas as pd
    state_path = _session_path(session_id)
    if not state_path.is_file():
        return None
    state = json.loads(state_path.read_text(encoding="utf-8"))
    ids = set(state.get("final_ids", state.get("selected_ids", [])))
    selected = [row for row in load_records() if row["appeal_id"] in ids]
    folder = Path(__file__).resolve().parents[3] / "data_store/cache/sessions" / safe_session_key(session_id) / "results"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"appeals_{uuid4().hex}.xlsx"
    pd.DataFrame(selected, columns=["appeal_id", "date", "prd", "s_prd", "chnl", "text"]).to_excel(path, index=False)
    return path
