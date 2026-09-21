"""Strict canonical/JSON parsing; structured errors never fall through."""
import json
import re
from datetime import date, datetime

from .models import AnalysisRequest, AnalysisRequestError
from .registry import MONEY_FILTERS


CANONICAL_MARKER = "Анализ ИОР"
CANONICAL_LABELS = (
    "Денежный показатель:",
    "Оргструктура:",
    "Период:",
    "Сформировать Excel:",
)


def normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AnalysisRequestError(f"Повторяющееся поле JSON: {key}.")
        result[key] = value
    return result


def _build_analysis_request(obj: dict) -> AnalysisRequest:
    """Единая финальная validation для canonical и JSON contracts."""
    unknown = set(obj) - {"action", "money_filter", "org_filter", "date_range", "export_excel"}
    if unknown:
        raise AnalysisRequestError("Неизвестные поля: " + ", ".join(sorted(unknown)))
    if not isinstance(obj.get("action"), str) or normalize(obj["action"]) != "анализ":
        raise AnalysisRequestError('Поле action должно иметь значение «Анализ».')
    money = obj.get("money_filter")
    if not isinstance(money, str) or normalize(money) not in MONEY_FILTERS:
        raise AnalysisRequestError("Обязательное поле money_filter: поддерживается «Прямые потери».")
    org = obj.get("org_filter")
    if org is not None and (not isinstance(org, str) or org.strip()):
        raise AnalysisRequestError("Фильтр по организационной структуре пока не поддерживается.")
    export = obj.get("export_excel", False)
    if type(export) is not bool:
        raise AnalysisRequestError("export_excel должен быть JSON boolean true/false.")
    period = obj.get("date_range")
    if not isinstance(period, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}:[0-9]{4}-[0-9]{2}-[0-9]{2}", period):
        raise AnalysisRequestError("Обязательное поле date_range: формат YYYY-MM-DD:YYYY-MM-DD.")
    try:
        start, end = map(date.fromisoformat, period.split(":"))
    except ValueError as exc:
        raise AnalysisRequestError("date_range содержит несуществующую дату.") from exc
    if start > end or end == date.max:
        raise AnalysisRequestError("Начало периода должно быть не позже конца; конец должен допускать следующий день.")
    return AnalysisRequest(normalize(money), start, end, export)


def _parse_canonical_fields(text: str) -> dict | None:
    """Парсит строгий human-readable envelope или возвращает None без маркера."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").strip().split("\n")
    if not lines or lines[0].strip() != CANONICAL_MARKER:
        return None

    values: dict[str, str] = {}
    index = 1
    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            continue
        if line not in CANONICAL_LABELS:
            raise AnalysisRequestError(f"Неизвестный или повреждённый label canonical-запроса: {line}.")
        if line in values:
            raise AnalysisRequestError(f"Повторяющийся label canonical-запроса: {line}")
        index += 1
        if index >= len(lines) or not lines[index].strip():
            raise AnalysisRequestError(f"Пустое значение canonical-поля: {line}")
        values[line] = lines[index].strip()
        index += 1

    missing = [label for label in CANONICAL_LABELS if label not in values]
    if missing:
        raise AnalysisRequestError("Отсутствуют обязательные canonical labels: " + ", ".join(missing))
    return values


def _parse_canonical_request(text: str) -> AnalysisRequest | None:
    fields = _parse_canonical_fields(text)
    if fields is None:
        return None

    period = fields["Период:"]
    match = re.fullmatch(r"([0-9]{2}\.[0-9]{2}\.[0-9]{4})\s+—\s+([0-9]{2}\.[0-9]{2}\.[0-9]{4})", period)
    if not match:
        raise AnalysisRequestError("Период canonical-запроса должен иметь формат DD.MM.YYYY — DD.MM.YYYY.")
    try:
        start, end = (datetime.strptime(value, "%d.%m.%Y").date() for value in match.groups())
    except ValueError as exc:
        raise AnalysisRequestError("Период canonical-запроса содержит несуществующую дату.") from exc

    excel_value = normalize(fields["Сформировать Excel:"])
    if excel_value == "да":
        export_excel = True
    elif excel_value == "нет":
        export_excel = False
    else:
        raise AnalysisRequestError("Сформировать Excel допускает только значения «Да» или «Нет».")

    org_value = fields["Оргструктура:"]
    org_filter = None if normalize(org_value) == "все" else org_value
    return _build_analysis_request({
        "action": "Анализ",
        "money_filter": fields["Денежный показатель:"],
        "org_filter": org_filter,
        "date_range": f"{start.isoformat()}:{end.isoformat()}",
        "export_excel": export_excel,
    })


def try_parse_analysis_request(prompt: str) -> AnalysisRequest | None:
    text = (prompt or "").strip().lstrip("\ufeff")
    canonical = _parse_canonical_request(text)
    if canonical is not None:
        return canonical

    # Also catch damaged/fenced contracts containing JSON field names.
    looks_structured = text.startswith(("{", "[", '"{')) or bool(
        re.search(r'["\'](?:action|money_filter|date_range|export_excel|org_filter)["\']\s*:', text)
    )
    if not looks_structured:
        return None
    try:
        obj = json.loads(text, object_pairs_hook=_object)
    except (ValueError, TypeError) as exc:
        raise AnalysisRequestError(f"Некорректный JSON запроса анализа: {exc}") from exc
    if not isinstance(obj, dict):
        raise AnalysisRequestError("Запрос анализа должен быть JSON-объектом.")
    return _build_analysis_request(obj)
