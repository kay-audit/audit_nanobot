"""Compact evidence, Markdown Qwen hypotheses, and factual fallback."""
import asyncio
import json
import logging
import re
from dataclasses import asdict

import pandas as pd

from .anomalies import report_events, select_event_incidents
from .models import AnomalyEvent

logger = logging.getLogger(__name__)
MAX_EVIDENCE_INCIDENTS = 60
MAX_INCIDENTS_PER_EVENT = 6
MAX_DESCRIPTION_CHARS = 1000
MAX_EVIDENCE_EVENTS = 12
EVIDENCE_BATCH_SIZE = 10
MAX_BATCH_CONCURRENCY = 3
TARGET_PROMPT_CHARS = 24_000
MAX_FINAL_PROMPT_CHARS = 40_000


def _clean(value, limit=300):
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return ""
    return " ".join(str(value).split())[:limit]


def _amount(value):
    return f"{float(value or 0):,.2f}".replace(",", " ").replace(".", ",")


def _compact_incident(row):
    summary = _clean(row.get("incdnt_summary_descr_txt"), MAX_DESCRIPTION_CHARS)
    full = _clean(row.get("incdnt_full_descr_txt"), MAX_DESCRIPTION_CHARS)
    description = summary
    if full and full.casefold() != summary.casefold():
        description = (summary + " — " + full).strip(" —")[:MAX_DESCRIPTION_CHARS]
    item = {key: row.get(key) for key in (
        "incdnt_id", "incdnt_sid", "direct_loss_rub", "amount_class",
        "org_struct_lvl_3_name", "risk_profile_id", "risk_profile_name", "process_lvl_4_name")}
    item["description"] = description
    return item


def _balanced_events(events):
    buckets = {}
    for event in events:
        family = event.dimension or event.kind
        buckets.setdefault(family, []).append(event)
    result = []
    while buckets:
        for family in list(buckets):
            result.append(buckets[family].pop(0))
            if not buckets[family]:
                del buckets[family]
    return result


def build_evidence_pack(data, metrics, events, request=None):
    """Select diverse LLM examples after full-population calculations."""
    balanced = _balanced_events(events)
    examples, bindings = {}, {}
    for event in balanced:
        rows = select_event_incidents(data, event)
        ordered = rows.sort_values(["direct_loss_rub", "incdnt_id"], ascending=[False, True], kind="stable")
        chosen_parts = [ordered.head(2)]
        if len(ordered) > 2:
            middle = len(ordered) // 2
            chosen_parts.extend([ordered.iloc[middle:middle + 2], ordered.tail(2)])
        if "months" in event.selector:
            for month in event.selector["months"]:
                ids = data.monthly_incident_df.loc[data.monthly_incident_df.month.eq(month), "incdnt_id"]
                month_rows = ordered.loc[ordered.incdnt_id.isin(ids)]
                chosen_parts.extend([month_rows.head(1), month_rows.iloc[len(month_rows) // 2:len(month_rows) // 2 + 1]])
        chosen = pd.concat(chosen_parts).drop_duplicates("incdnt_id").head(MAX_INCIDENTS_PER_EVENT)
        bindings[event.event_id] = []
        for row in chosen.to_dict("records"):
            key = str(row["incdnt_id"])
            if key not in examples and len(examples) >= MAX_EVIDENCE_INCIDENTS:
                continue
            examples.setdefault(key, _compact_incident(row))
            bindings[event.event_id].append(key)
    supported = [event for event in balanced if bindings.get(event.event_id)]
    if not supported:
        row = data.approved_incident_df.iloc[0].to_dict()
        key = str(row["incdnt_id"])
        examples[key] = _compact_incident(row)
        event = AnomalyEvent("signal-case", "case_observation", 10,
                             "Доступен отдельный случай; устойчивые закономерности не установлены.",
                             {"amount_class": row.get("amount_class")}, {"incdnt_id": row["incdnt_id"]})
        supported, bindings[event.event_id] = [event], [key]
    selected = set(examples)
    monthly = data.monthly_incident_df.loc[data.monthly_incident_df.incdnt_id.astype(str).isin(selected)]
    by_case = {str(key): rows[["month", "direct_loss_rub", "amount_class"]].to_dict("records")
               for key, rows in monthly.groupby("incdnt_id", sort=False)}
    for key, item in examples.items():
        item["months"] = by_case.get(key, [])
        item["selected_for"] = [event.event_id for event in supported if key in bindings[event.event_id]]
    fields = ["month", "unique_incidents", "direct_loss_rub", "is_partial_month"]
    payload = {
        "available_incidents": len(data.approved_incident_df),
        "statistics": {
            "period": {"start": str(request.start), "end": str(request.end)} if request else None,
            "approved_incidents": len(data.approved_incident_df),
            "approved_loss": metrics.approved_loss,
            "monthly": metrics.monthly[fields].to_dict("records"),
        },
        "events": [{key: value for key, value in
                    (asdict(event) | {"evidence_keys": bindings[event.event_id]}).items()
                    if key not in {"selector", "evidence_incident_ids"}} for event in supported],
        "incidents": list(examples.values()),
    }
    return json.loads(json.dumps(payload, ensure_ascii=False, default=str), parse_constant=lambda _: None)


SYSTEM_PROMPT = """Ты — эксперт-аналитик Службы внутреннего аудита.
На основании рассчитанных фактов и описаний реальных ИОР сформулируй ровно три компактные,
содержательные и разные гипотезы для аудиторской проверки.
Все показатели рассчитаны Python. Не пересчитывай и не придумывай суммы, проценты, даты или EVE-ID.
Закономерности не являются доказанными причинами: предложи возможный проверяемый механизм и способ проверки.
Опирайся на конкретные наблюдения. Не используй универсальные шаблоны, если факты позволяют предметную гипотезу.
Не повторяй одну мысль. Верни только Section 3 в Markdown, без code fences и дополнительных разделов."""

FORMAT = """Верни строго этот формат:
### 3. Гипотезы

**Гипотеза 1: <конкретное название>**
- **Предположение / Суть проблемы:** ...
- **Шаги проверки:**
  1. ...
  2. ...
- **Ожидаемый результат:** ...

Аналогично оформи гипотезы 2 и 3. В каждой должно быть минимум два нумерованных шага."""


def _incident_text(row, description_limit=MAX_DESCRIPTION_CHARS):
    risk = " / ".join(filter(None, [_clean(row.get("risk_profile_id")), _clean(row.get("risk_profile_name"))]))
    return "\n".join([
        _clean(row.get("incdnt_sid")) or f"ИОР {row.get('incdnt_id')}",
        f"ТБ: {_clean(row.get('org_struct_lvl_3_name')) or 'не указано'}",
        f"ЦПР: {risk or 'не указано'}",
        f"Процесс: {_clean(row.get('process_lvl_4_name')) or 'не указано'}",
        f"Прямая потеря: {_amount(row.get('direct_loss_rub'))} руб.",
        f"Описание: {_clean(row.get('description'), description_limit) or 'не указано'}",
    ])


def _base_prompt(pack, event_limit=MAX_EVIDENCE_EVENTS, event_chars=1000):
    period = pack["statistics"].get("period") or {}
    facts = [f"{i}. {_clean(event.get('description'), event_chars)}"
             for i, event in enumerate(pack["events"][:event_limit], 1)]
    return (
        f"ПЕРИОД:\n{period.get('start', 'не указано')}–{period.get('end', 'не указано')}.\n\n"
        "Анализируются утверждённые ИОР с прямыми потерями. Рассчитанные закономерности являются "
        "основаниями для проверки, а не доказанными причинами.\n\nОСНОВНЫЕ НАБЛЮДЕНИЯ:\n" +
        ("\n\n".join(facts) or "Устойчивые закономерности не установлены.") +
        "\n\nПРИМЕРЫ РЕАЛЬНЫХ ИОР:\n")


def _build_prompt(pack, feedback="", summaries=None):
    suffix = ("\n\nСформулируй три разные конкретные и проверяемые гипотезы. Не пересказывай "
              "наблюдения: предложи возможные механизмы для проверки.\n\n" + FORMAT)
    if feedback:
        suffix += ("\n\nПРЕДЫДУЩИЙ ОТВЕТ НЕ ПРОШЁЛ ПРОВЕРКУ:\n" + feedback +
                   "\nСформируй полный раздел заново и исправь указанные ошибки.")
    base, blocks = _base_prompt(pack), []
    if summaries:
        base += "\nАНАЛИТИЧЕСКИЕ ВЫЖИМКИ ПО ГРУППАМ ИОР:\n" + "\n\n".join(
            f"Группа {index}:\n{_clean(summary, 2200)}" for index, summary in enumerate(summaries, 1)) + \
            "\n\nКЛЮЧЕВЫЕ ПРЯМЫЕ ПРИМЕРЫ:\n"
        incident_rows = pack["incidents"][:6]
    else:
        incident_rows = pack["incidents"]
    for row in incident_rows:
        block = _incident_text(row, 500)
        if len(SYSTEM_PROMPT) + len(base + "\n\n".join(blocks + [block]) + suffix) > TARGET_PROMPT_CHARS:
            break
        blocks.append(block)
    prompt = base + "\n\n".join(blocks) + suffix
    if len(SYSTEM_PROMPT) + len(prompt) > MAX_FINAL_PROMPT_CHARS:
        base = _base_prompt(pack, event_limit=8, event_chars=400)
        while blocks and len(SYSTEM_PROMPT) + len(base + "\n\n".join(blocks) + suffix) > MAX_FINAL_PROMPT_CHARS:
            blocks.pop()
        prompt = base + "\n\n".join(blocks) + suffix
    # Safety for pathological source labels: truncate text, never abort the Qwen call.
    prompt = prompt[:MAX_FINAL_PROMPT_CHARS - len(SYSTEM_PROMPT)]
    return prompt, len(blocks)


def build_hypothesis_messages(pack, feedback="", summaries=None):
    prompt, _ = _build_prompt(pack, feedback, summaries)
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]


BATCH_SYSTEM_PROMPT = """Ты — аудитор-аналитик. Кратко проанализируй переданные реальные ИОР:
найди повторяющиеся обстоятельства, процессы, сценарии и существенные различия. Не строй финальные гипотезы,
не придумывай причины, числа или EVE. Верни аналитическую выжимку до 2000 символов."""


def _batch_prompt(rows):
    return ("Проанализируй эту группу ИОР. Сохраняй реальные EVE только там, где они помогают сопоставлению.\n\n" +
            "\n\n".join(_incident_text(row, MAX_DESCRIPTION_CHARS) for row in rows))


async def summarize_evidence_batches(pack, ask):
    rows = pack["incidents"]
    batches = [rows[index:index + EVIDENCE_BATCH_SIZE] for index in range(0, len(rows), EVIDENCE_BATCH_SIZE)]
    logger.info("[analysis_mode] evidence selection: selected_incidents=%d available_incidents=%d",
                len(rows), pack.get("available_incidents", len(rows)))
    semaphore = asyncio.Semaphore(MAX_BATCH_CONCURRENCY)

    async def run_batch(index, batch):
        prompt = _batch_prompt(batch)
        logger.info("[analysis_mode] evidence batch: batch=%d/%d incidents=%d prompt_chars=%d",
                    index + 1, len(batches), len(batch), len(BATCH_SYSTEM_PROMPT) + len(prompt))
        try:
            async with semaphore:
                response = str(await asyncio.to_thread(ask, [
                    {"role": "system", "content": BATCH_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}], max_tokens=1800) or "").strip()
            if not response or response.startswith("Анализ выполнен на основе имеющихся метрик"):
                raise ValueError("empty or technical fallback response")
            logger.info("[analysis_mode] evidence batch completed: batch=%d/%d response_chars=%d",
                        index + 1, len(batches), len(response))
            return response
        except Exception as exc:
            logger.warning("[analysis_mode] evidence batch %d/%d failed, continuing with remaining evidence: %s",
                           index + 1, len(batches), exc)
            return ""
    return [summary for summary in await asyncio.gather(
        *(run_batch(index, batch) for index, batch in enumerate(batches))) if summary]


def _strip_fences(text):
    value = str(text or "").strip()
    fence = chr(96) * 3
    if value.startswith(fence):
        lines = value.splitlines()
        lines = lines[1:] if lines and lines[0].startswith(fence) else lines
        lines = lines[:-1] if lines and lines[-1].strip().startswith(fence) else lines
        value = "\n".join(lines).strip()
    marker = re.search(r"(?im)^###\s*3\.\s*Гипотезы\s*$", value)
    return value[marker.start():].strip() if marker else value


def validate_response(response, pack):
    """Validate human Markdown without internal signal linkage or JSON."""
    text, errors = _strip_fences(response), []
    headers = re.findall(r"(?m)^###\s+(.+?)\s*$", text)
    if headers != ["3. Гипотезы"]:
        errors.append("ответ должен содержать только заголовок «### 3. Гипотезы»")
    blocks = re.split(r"(?=\*\*Гипотеза\s+\d+\s*:)", text, flags=re.I)
    blocks = [block for block in blocks if re.match(r"\*\*Гипотеза\s+\d+\s*:", block, re.I)]
    numbers = [int(re.match(r"\*\*Гипотеза\s+(\d+)", block, re.I).group(1)) for block in blocks]
    if numbers != [1, 2, 3]:
        errors.append(f"получены гипотезы {numbers or 'без номеров'} вместо 1, 2, 3")
    for number, block in zip(numbers, blocks):
        missing = []
        for pattern, label in ((r"Предположение\s*/\s*Суть проблемы", "Предположение / Суть проблемы"),
                               (r"Шаги проверки", "Шаги проверки"),
                               (r"Ожидаемый результат", "Ожидаемый результат")):
            if not re.search(pattern, block, re.I):
                missing.append(label)
        if len(re.findall(r"(?m)^\s*(?:[-*]\s*)?\d+[.)]\s+", block)) < 2:
            missing.append("минимум два нумерованных шага")
        if missing:
            errors.append(f"гипотеза {number}: отсутствует {', '.join(missing)}")
    allowed = {_clean(row.get("incdnt_sid")).upper() for row in pack["incidents"] if row.get("incdnt_sid")}
    mentioned = {value.upper() for value in re.findall(r"EVE-[A-Za-z0-9_-]+", text, re.I)}
    unknown = sorted(mentioned - allowed)
    if unknown:
        errors.append("неизвестные EVE-ID: " + ", ".join(unknown))
    return (None if errors else text), errors


def _fallback_title(event, angle):
    subject = _clean(event.get("category")) or (f"наблюдение за {_clean(event.get('month'))}"
                                                if event.get("month") else _clean(event.get("description"), 110).rstrip("."))
    return f"{angle}: {subject}"


def fallback_hypotheses(pack):
    events, chosen, families = pack["events"], [], set()
    for event in events:
        family = event.get("dimension") or event.get("kind")
        if family not in families:
            chosen.append(event); families.add(family)
        if len(chosen) == 3:
            break
    while len(chosen) < 3:
        chosen.append(events[len(chosen) % len(events)])
    parts = ["### 3. Гипотезы"]
    for index, event in enumerate(chosen, 1):
        angle = ("Документальные признаки", "Динамика наблюдения", "Условия процесса")[index - 1]
        keys = event.get("evidence_keys", [])[:2]
        eves = [str(row.get("incdnt_sid")) for row in pack["incidents"]
                if str(row["incdnt_id"]) in keys and row.get("incdnt_sid")]
        reference = f" Для первичной проверки можно использовать {', '.join(eves)}." if eves else ""
        parts.append(
            f"**Гипотеза {index}: {_fallback_title(event, angle)}**\n\n"
            f"- **Предположение / Суть проблемы:** Наблюдение «{_clean(event['description'], 600)}» может "
            f"указывать на повторяющуюся особенность данных или процесса; причина не установлена.{reference}\n"
            "- **Шаги проверки:**\n"
            "  1. Сопоставить выбранные ИОР с первичными документами и обстоятельствами наблюдения.\n"
            "  2. Проверить повторяемость признака по подразделению, ЦПР, процессу и периоду.\n"
            "- **Ожидаемый результат:** Повторяемые документальные признаки поддержат расширение проверки; "
            "их отсутствие опровергнет предположение для выбранных случаев.")
    return "\n\n".join(parts)


async def generate_hypotheses(pack, ask=None):
    if ask is None:
        try:
            from utils.local_qwen import ask_local_qwen
            ask = ask_local_qwen
        except ImportError as exc:
            logger.warning("[analysis_mode] hypotheses_source=fallback reason=import_error:%s", exc)
            return fallback_hypotheses(pack)
    summaries = await summarize_evidence_batches(pack, ask)
    feedback, reason = "", "unknown"
    for attempt in range(1, 3):
        prompt, evidence_count = _build_prompt(pack, feedback, summaries)
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
        logger.info("[analysis_mode] final hypotheses request: batch_summaries=%d anomaly_facts=%d prompt_chars=%d attempt=%d",
                    len(summaries), min(len(pack["events"]), MAX_EVIDENCE_EVENTS),
                    sum(len(message["content"]) for message in messages), attempt)
        try:
            response = str(await asyncio.to_thread(ask, messages, max_tokens=8192) or "")
            accepted, errors = validate_response(response, pack)
            logger.info("[analysis_mode] Qwen hypothesis response: response_chars=%d complete=%s",
                        len(response), accepted is not None)
            if accepted is not None:
                logger.info("[analysis_mode] hypotheses_source=qwen")
                return accepted
            reason = "; ".join(errors)
            logger.warning("[analysis_mode] hypothesis validation failed: reason=%s", reason)
            feedback = reason
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            logger.warning("[analysis_mode] Qwen hypothesis call failed: reason=%s", reason)
            break
    logger.warning("[analysis_mode] hypotheses_source=fallback reason=%s", reason)
    return fallback_hypotheses(pack)
