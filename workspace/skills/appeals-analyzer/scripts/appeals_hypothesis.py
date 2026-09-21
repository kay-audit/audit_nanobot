"""Batched, grounded root-cause analysis and session follow-up for appeals."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from typing import Any, Dict, List, Optional, Sequence, Set

import pandas as pd

try:
    from ..utils.local_qwen import def_ask_gigachat
    from ..utils.pipeline_config import CONFIG
except ImportError:
    from utils.local_qwen import def_ask_gigachat
    from utils.pipeline_config import CONFIG

try:
    from .appeals_profiler import (
        build_loaded_columns_profile,
        format_loaded_columns_profile,
        profile_complaints_dataframe,
    )
except ImportError:
    from appeals_profiler import (
        build_loaded_columns_profile,
        format_loaded_columns_profile,
        profile_complaints_dataframe,
    )

logger = logging.getLogger(__name__)
EVIDENCE_FIELDS = (
    "observed_patterns", "root_cause_signals", "evidence", "representative_ids",
    "counterexamples", "possible_actions",
)


def _get_ask_llm_fn():
    return def_ask_gigachat


def _sanitize_narrative_percentages(text: str, total_count: int) -> str:
    if not text or total_count <= 0:
        return text
    def replace(match):
        count = int(match.group(1).replace(" ", "").replace(",", ""))
        return f"{count:,} из {total_count:,} обращений ({count / total_count * 100:.1f}%)"
    return re.sub(r"(\d+[\s\d]*)\s+из\s+\d+[\s\d]*\s+обращени[йя]", replace, text)


def validate_narrative_grounding(text: str, allowed_ids: Sequence[str]) -> tuple[bool, List[str]]:
    allowed = {_normalize_id(value) for value in allowed_ids}
    referenced = {
        match.group(1).rstrip(".,;:")
        for match in re.finditer(
            r"(?i)(?:\bID\s*[:#№]\s*|\bобращени[ея]\s*[#№:]\s*)([\w.-]+)",
            text or "",
        )
    }
    invalid = sorted(value for value in referenced if value and value not in allowed)
    return not invalid, invalid


def _build_fallback_narrative_report(user_msg: str, df: pd.DataFrame, profile_text: str) -> str:
    total = len(df)
    return (
        f"### Аналитический отчёт\nПо запросу «{user_msg}» сформирована выгрузка из {total:,} обращений.\n\n"
        f"{profile_text}\n\n### Гипотезы\n"
        "\n**Гипотеза 1. Основная проблема сосредоточена в крупнейшей теме обращений.**\n"
        "Что наблюдается: в математическом профиле одна или несколько тем заметно опережают остальные по числу обращений. "
        "Это может означать, что клиентам регулярно мешает один и тот же этап обслуживания.\n"
        "Что проверить: разобрать обращения крупнейшей темы по шагам клиентского пути, определить момент возникновения проблемы "
        "и сравнить фактический порядок обслуживания с установленным.\n"
        "\n**Гипотеза 2. Клиенты обращаются повторно, потому что вопрос не решается с первого раза.**\n"
        "Что наблюдается: обращения могут описывать повторные запросы, ожидание ответа или необходимость снова предоставлять сведения. "
        "Возможная причина — отсутствие понятного результата после первого контакта или несогласованные действия исполнителей.\n"
        "Что проверить: найти повторные обращения одних клиентов, сопоставить даты и содержание ответов, проверить полноту первого решения.\n"
        "\n**Гипотеза 3. Часть жалоб возникает из-за непонятных правил и объяснений.**\n"
        "Что наблюдается: клиент может не понимать условия услуги, причину решения или дальнейшие действия. "
        "Даже корректное решение в такой ситуации воспринимается как отказ или ошибка.\n"
        "Что проверить: изучить шаблоны ответов и уведомлений, проверить, указаны ли причина решения, срок и следующий шаг клиента.\n"
        "\n**Гипотеза 4. Рост обращений в отдельные периоды связан с изменением процесса или повышенной нагрузкой.**\n"
        "Что наблюдается: временная динамика может показывать заметные пики относительно соседних периодов. "
        "Причиной могут быть изменение условий, техническая проблема или увеличение времени обработки.\n"
        "Что проверить: сопоставить даты пиков с релизами, изменениями правил и показателями нагрузки, затем отдельно разобрать темы этих обращений."
    )


def _remove_sampling_disclosures(text: str) -> str:
    """Keep internal evidence sampling out of the user-facing analytical result."""
    forbidden = re.compile(
        r"(?i)(uncertaint|неопредел[её]нност|ограничени\w*\s+выборк|"
        r"репрезентативност|(?:batch|батч|structured summar)|"
        r"(?:передан|отобран)\w*.{0,80}(?:языков\w*\s+модел|llm|текст)|"
        r"(?:300|тр[её]хсот)\s+обращени\w*.{0,80}(?:анализ|выборк|передан)|"
        r"(?:анализ|выборк|передан).{0,80}(?:300|тр[её]хсот)\s+обращени)"
    )
    forbidden_heading = re.compile(r"(?i)^\s*#{1,6}\s*(uncertaint|неопредел[её]нност|ограничени\w*\s+выборк)")
    heading = re.compile(r"^\s*#{1,6}\s+")
    cleaned: List[str] = []
    skip_section = False
    for line in (text or "").splitlines():
        if forbidden_heading.search(line):
            skip_section = True
            continue
        if skip_section and heading.search(line):
            skip_section = False
        if not skip_section and not forbidden.search(line):
            cleaned.append(line)
    return "\n".join(cleaned).strip()


def _normalize_hypothesis_language(text: str) -> str:
    """Remove implementation vocabulary from an otherwise grounded hypothesis narrative."""
    replacements = {
        r"(?i)structured\s+summar(?:y|ies)": "тексты обращений",
        r"(?i)\bsummaries\b": "тексты обращений",
        r"(?i)\bevidence\b": "примеры обращений",
        r"(?i)\bpatterns?\b": "повторяющиеся ситуации",
        r"(?i)\bs_prd\b": "субпродукт",
        r"(?i)\bs_subj\b": "подтема",
        r"(?i)\breq_status\b": "статус обращения",
        r"(?i)\bsubj\b": "тема",
        r"(?i)\bgrp\b": "группа обращений",
    }
    result = text or ""
    for pattern, replacement in replacements.items():
        result = re.sub(pattern, replacement, result)
    result = re.sub(r"(?im)^\s*#{1,6}\s*Гипотезы\s*$", "", result)
    return re.sub(r"\n{3,}", "\n\n", result).strip()


def _has_four_detailed_hypotheses(text: str) -> bool:
    numbers = {
        int(value)
        for value in re.findall(r"(?im)^\s*(?:\*{0,2})?Гипотеза\s+([1-4])\b", text or "")
    }
    return numbers == {1, 2, 3, 4} and len(text or "") >= 1200


def _normalize_id(value: Any) -> str:
    return str(value).strip()


def _extract_json_object(raw: Any) -> Dict[str, Any]:
    text = str(raw or "").strip()
    candidates = [text]
    fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text, re.IGNORECASE)
    if fenced:
        candidates.append(fenced.group(1))
    first, last = text.find("{"), text.rfind("}")
    if 0 <= first < last:
        candidates.append(text[first:last + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    raise ValueError("LLM evidence response contains no valid JSON object")


def _ground_ids(value: Any, allowed_ids: Set[str], invalid_ids: Set[str], key: str = "") -> Any:
    id_key = key.casefold() in {"id", "ids", "appeal_id", "appeal_ids", "representative_ids"}
    if isinstance(value, dict):
        return {item_key: _ground_ids(item_value, allowed_ids, invalid_ids, item_key) for item_key, item_value in value.items()}
    if isinstance(value, list):
        grounded = []
        for item in value:
            normalized = _normalize_id(item) if id_key and not isinstance(item, (dict, list)) else None
            if normalized is not None:
                if normalized in allowed_ids:
                    grounded.append(normalized)
                elif normalized:
                    invalid_ids.add(normalized)
            else:
                grounded.append(_ground_ids(item, allowed_ids, invalid_ids, key))
        return grounded
    if id_key and value is not None:
        normalized = _normalize_id(value)
        if normalized in allowed_ids:
            return normalized
        if normalized:
            invalid_ids.add(normalized)
        return None
    return value


def validate_evidence_payload(payload: Dict[str, Any], allowed_ids: Sequence[str], batch_number: int) -> Dict[str, Any]:
    allowed = {_normalize_id(value) for value in allowed_ids}
    source_ids = list(dict.fromkeys(_normalize_id(value) for value in allowed_ids if _normalize_id(value)))
    invalid_ids: Set[str] = set()
    schema_errors: List[str] = []
    if not isinstance(payload, dict):
        payload = {}
        schema_errors.append("payload must be an object")
    for field in EVIDENCE_FIELDS:
        if field not in payload:
            schema_errors.append(f"missing required field: {field}")
        elif not isinstance(payload[field], list):
            schema_errors.append(f"field {field} must be an array")
    result: Dict[str, Any] = {
        "batch": batch_number,
        "status": "invalid_schema" if schema_errors else "ok",
        "_source_ids": source_ids,
    }
    for field in EVIDENCE_FIELDS:
        value = payload.get(field, []) if isinstance(payload.get(field, []), list) else []
        if field == "evidence":
            evidence = []
            for index, item in enumerate(value):
                if not isinstance(item, dict):
                    schema_errors.append(f"evidence[{index}] must be an object")
                    continue
                statement, appeal_ids = item.get("statement"), item.get("appeal_ids")
                if not isinstance(statement, str) or not statement.strip():
                    schema_errors.append(f"evidence[{index}].statement must be a non-empty string")
                    continue
                if not isinstance(appeal_ids, list):
                    schema_errors.append(f"evidence[{index}].appeal_ids must be an array")
                    continue
                if any(isinstance(value, (dict, list)) for value in appeal_ids):
                    schema_errors.append(f"evidence[{index}].appeal_ids must contain scalar IDs")
                    continue
                evidence.append({
                    "statement": statement.strip(),
                    "appeal_ids": _ground_ids(appeal_ids, allowed, invalid_ids, "appeal_ids"),
                })
            result[field] = evidence
        else:
            result[field] = _ground_ids(value, allowed, invalid_ids, field)
    if any(isinstance(value, (dict, list)) for value in payload.get("representative_ids", []) if isinstance(payload.get("representative_ids"), list)):
        schema_errors.append("representative_ids must contain scalar IDs")
        result["representative_ids"] = []
    result["representative_ids"] = list(dict.fromkeys(result["representative_ids"]))
    if schema_errors:
        result["status"] = "invalid_schema"
        result["schema_errors"] = schema_errors
    if invalid_ids:
        result["invalid_ids_removed"] = sorted(invalid_ids)
    return result


def _failed_evidence(batch_number: int, error: Exception | str, source_ids: Sequence[str] = ()) -> Dict[str, Any]:
    return {
        "batch": batch_number, "status": "failed", "error": str(error),
        "_source_ids": list(dict.fromkeys(_normalize_id(value) for value in source_ids)),
        **{field: [] for field in EVIDENCE_FIELDS},
    }


def select_dialogue_context(dialogue: Any, max_chars: int) -> str:
    text = str(dialogue or "")
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    marker = "\n…[середина опущена]…\n"
    usable = max(0, max_chars - len(marker))
    prefix_chars = int(usable * 0.7)
    suffix_chars = usable - prefix_chars
    return text[:prefix_chars] + marker + (text[-suffix_chars:] if suffix_chars else "")


def _sampling_strata(df: pd.DataFrame) -> pd.Series:
    """Build bounded business strata from available columns, without using text or LLM."""
    lookup = {str(column).strip().casefold(): column for column in df.columns}
    groups = (
        ("subj", "s_subj", "grp", "topic", "theme", "тема", "короткое описание", "short_description"),
        ("prd", "product", "продукт"),
        ("s_prd", "subproduct", "субпродукт"),
    )
    selected = []
    for aliases in groups:
        column = next((lookup[alias] for alias in aliases if alias in lookup), None)
        if column is None:
            continue
        values = df[column].fillna("∅").astype(str).str.strip().replace("", "∅")
        # Do not turn a nearly unique description/identifier into thousands of strata.
        if 1 < values.nunique() <= max(50, int(math.sqrt(max(len(df), 1))) * 4):
            selected.append(values)
        if len(selected) == 3:
            break

    date_column = next((lookup[name] for name in ("date", "created", "created_at", "дата") if name in lookup), None)
    if date_column is not None:
        months = pd.to_datetime(df[date_column], errors="coerce").dt.to_period("M").astype(str).replace("NaT", "∅")
        if 1 < months.nunique() <= 60:
            selected.append(months)
    if not selected:
        return pd.Series("all", index=df.index, dtype="object")
    return pd.concat(selected, axis=1).astype(str).agg(" | ".join, axis=1)


def select_hypothesis_sample(
    df: pd.DataFrame,
    fraction: float | None = None,
    max_items: int | None = None,
) -> pd.DataFrame:
    """Select an exact-size deterministic stratified sample for qualitative LLM evidence."""
    if df.empty:
        return df.copy()
    if fraction is None:
        target = min(len(df), max(1, int(max_items or CONFIG.hypothesis_sample_size)))
    else:
        sample_fraction = min(1.0, max(0.0, float(fraction)))
        target = min(len(df), max(1, int(math.ceil(len(df) * sample_fraction))))
    if target == len(df):
        return df.copy().reset_index(drop=True)

    work = df.copy()
    work["_hypothesis_stratum"] = _sampling_strata(work)
    id_values = work["id"].map(_normalize_id) if "id" in work else work.index.astype(str)
    work["_hypothesis_order"] = pd.util.hash_pandas_object(id_values, index=False).astype("uint64")
    sizes = work.groupby("_hypothesis_stratum", sort=True).size()
    expected = sizes.astype(float) * target / len(work)
    quotas = expected.apply(math.floor).astype(int)

    # Preserve rare represented strata when the target has enough slots.
    if len(sizes) <= target:
        quotas = quotas.clip(lower=1)
    quotas = pd.concat([quotas, sizes], axis=1).min(axis=1).astype(int)
    while int(quotas.sum()) > target:
        removable = quotas[quotas > 1]
        if removable.empty:
            removable = quotas[quotas > 0]
        key = sorted(removable.index, key=lambda value: (expected[value] - quotas[value], str(value)))[0]
        quotas[key] -= 1
    while int(quotas.sum()) < target:
        available = quotas[quotas < sizes]
        key = sorted(available.index, key=lambda value: (-(expected[value] - quotas[value]), str(value)))[0]
        quotas[key] += 1

    selected_indices = []
    for stratum, group in work.groupby("_hypothesis_stratum", sort=True):
        selected_indices.extend(
            group.sort_values(["_hypothesis_order"], kind="stable").head(int(quotas[stratum])).index.tolist()
        )
    return df.loc[selected_indices].copy().reset_index(drop=True)


def build_evidence_batches(df: pd.DataFrame) -> List[Dict[str, Any]]:
    batches: List[Dict[str, Any]] = []
    current_items: List[str] = []
    current_ids: List[str] = []
    chars = 0
    for _, row in df.iterrows():
        appeal_id = _normalize_id(row.get("id"))[:200]
        dialogue = row.get("Транскрибация диалога", row.get("msg_pprb_chat", row.get("description", "")))
        description = str(row.get("Короткое описание", row.get("short_description", "")))
        appeal_text = f"Описание: {description}\nДиалог: {dialogue}"
        header = f"ID: {appeal_id}\nДата: {str(row.get('date', '—'))[:100]}\nТекст обращения:\n"
        appeal_text_budget = min(
            CONFIG.hypothesis_chars_per_appeal,
            max(0, CONFIG.hypothesis_batch_char_budget - len(header)),
        )
        item = header + select_dialogue_context(appeal_text, appeal_text_budget)
        separator_cost = 5 if current_items else 0
        if current_items and (
            len(current_items) >= CONFIG.hypothesis_batch_size
            or chars + separator_cost + len(item) > CONFIG.hypothesis_batch_char_budget
        ):
            batches.append({"ids": current_ids, "items": current_items})
            current_items, current_ids, chars = [], [], 0
            separator_cost = 0
        current_items.append(item)
        current_ids.append(appeal_id)
        chars += separator_cost + len(item)
    if current_items:
        batches.append({"ids": current_ids, "items": current_items})
    return batches


async def analyze_evidence_batch(
    batch: Dict[str, Any],
    batch_number: int,
    ask_llm=None,
) -> Dict[str, Any]:
    ask = ask_llm or _get_ask_llm_fn()
    prompt = (
        "Сделай содержательное summary текущего batch обращений и верни СТРОГО один JSON-объект с массивами: "
        + ", ".join(EVIDENCE_FIELDS)
        + '. Каждый evidence item: {"statement":"...","appeal_ids":["..."]}. '
        "appeal_ids должны содержать только ID из текущего batch. "
        "Сгруппируй повторяющиеся темы, сохрани подтверждающие факты, контрпримеры и возможные действия. "
        "Не придумывай counts, статистику или причинность: точные количества будут добавлены отдельно из полного массива.\n\n"
        + "\n---\n".join(batch["items"])
    )
    try:
        raw = await asyncio.to_thread(ask, [{"role": "system", "content": prompt}])
        return validate_evidence_payload(_extract_json_object(raw), batch["ids"], batch_number)
    except Exception as exc:
        logger.warning("Evidence batch %s failed: %s", batch_number, exc)
        return _failed_evidence(batch_number, exc, batch.get("ids", []))


def _public_evidence(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _public_evidence(item) for key, item in value.items() if not key.startswith("_")}
    if isinstance(value, list):
        return [_public_evidence(item) for item in value]
    return value


async def reduce_structured_evidence(payloads: List[Dict[str, Any]], ask_llm=None) -> List[Dict[str, Any]]:
    ask = ask_llm or _get_ask_llm_fn()
    current = list(payloads)
    while len(json.dumps(_public_evidence(current), ensure_ascii=False, default=str)) > CONFIG.hypothesis_reduce_char_budget and len(current) > 1:
        reduced: List[Dict[str, Any]] = []
        for group_number, start in enumerate(range(0, len(current), 8), 1):
            group = current[start:start + 8]
            allowed_ids = list(dict.fromkeys(
                _normalize_id(value)
                for payload in group
                for value in payload.get("_source_ids", [])
            ))
            prompt = (
                "Сведи structured evidence ниже в один СТРОГИЙ JSON с теми же полями. "
                'Evidence item имеет schema {"statement":"...","appeal_ids":["..."]}. '
                "Сохрани основные patterns, counterexamples, possible_actions и только source evidence IDs.\n"
                + json.dumps(_public_evidence(group), ensure_ascii=False, default=str)
            )
            try:
                raw = await asyncio.to_thread(ask, [{"role": "system", "content": prompt}])
                reduced.append(validate_evidence_payload(_extract_json_object(raw), allowed_ids, group_number))
            except Exception as exc:
                # Explicitly retain every source group instead of silently dropping its tail.
                reduced.append({
                    "batch": group_number, "status": "reduce_failed", "error": str(exc),
                    "_source_ids": allowed_ids,
                    "source_batches": group,
                    **{field: [] for field in EVIDENCE_FIELDS},
                })
        current = reduced
    return current


async def generate_complaint_hypothesis_narrative(
    user_msg: str,
    df: pd.DataFrame,
    file_info: dict = None,
    total_db_count: int = None,
    **_: Any,
) -> str:
    if df.empty:
        return "По вашему запросу не найдено подходящих обращений."
    total = len(df)
    profile = profile_complaints_dataframe(df, df_batch=pd.DataFrame(), total_db_count=total)
    columns_profile = build_loaded_columns_profile(
        df,
        max_columns=CONFIG.hypothesis_profile_max_columns,
        top_values=CONFIG.hypothesis_profile_top_values,
    )
    sample = select_hypothesis_sample(df)
    batches = build_evidence_batches(sample)
    logger.info(
        "[appeals_hypothesis] population=%s sample=%s fraction=%.3f evidence_batches=%s coverage=%s profile_columns=%s",
        total, len(sample), len(sample) / total, len(batches),
        sum(len(batch["ids"]) for batch in batches), len(columns_profile["columns"]),
    )
    ask = _get_ask_llm_fn()
    evidence = [
        await analyze_evidence_batch(batch, number, ask)
        for number, batch in enumerate(batches, 1)
    ]
    reduced = await reduce_structured_evidence(evidence, ask)
    hypothesis_prompt = (
        f"Сформируй четыре подробные и разные гипотезы по запросу: {user_msg}\n"
        f"Ниже приведён точный математический профиль всех {total} обращений:\n{profile}\n"
        "Дополнительные точные распределения по полному массиву; любые количества можно брать только отсюда:\n"
        + format_loaded_columns_profile(columns_profile)
        + "\nСодержание и примеры из текстов обращений:\n"
        + json.dumps(_public_evidence(reduced), ensure_ascii=False, default=str)
        + "\nВерни только четыре блока с заголовками «Гипотеза 1» — «Гипотеза 4», без вводного отчёта и таблиц. "
        "Каждая гипотеза должна быть самостоятельной и подробной: ясный заголовок; что именно происходит; "
        "какие факты и конкретные примеры на это указывают; простое объяснение возможной причины; "
        "какой ущерб или неудобство получает клиент; что конкретно проверить для подтверждения. "
        "Пиши простым деловым русским языком. Не используй придуманные названия процессов, систем или методик. "
        "Избегай слов «паттерн», «сигнал», «драйвер», «когорта», «сегмент», «evidence», «summary» и технических имён колонок. "
        "Не повторяй одну мысль разными словами и не пиши общие фразы без связи с содержанием обращений. "
        "Точные количества используй только из полного математического профиля. Не придумывай IDs, количества или причинность; "
        "ссылки оформляй только как ID: <точный ID из предоставленных примеров>. "
        "Никогда не описывай внутреннюю методику отбора текстов, число текстов, переданных языковой модели, "
        "батчи, репрезентативность, ограничения выборки или неопределённости."
    )
    for attempt in range(3):
        try:
            prompt = hypothesis_prompt
            if attempt:
                prompt += (
                    "\nПредыдущая попытка не прошла проверку. Дай заново ровно четыре подробные гипотезы, "
                    "каждую начни с отдельной строки «Гипотеза N» и раскрой всеми обязательными пунктами."
                )
            result = str(await asyncio.to_thread(ask, [{"role": "system", "content": prompt}])).strip()
            result = _normalize_hypothesis_language(_remove_sampling_disclosures(result))
            if result:
                grounded, invalid_ids = validate_narrative_grounding(result, sample["id"].map(_normalize_id).tolist())
                if grounded and _has_four_detailed_hypotheses(result):
                    hypotheses = _sanitize_narrative_percentages(result, total)
                    return (
                        f"### Аналитический отчёт\nПо запросу «{user_msg}» сформирована выгрузка "
                        f"из {total:,} обращений.\n\n{profile}\n\n### Гипотезы\n\n{hypotheses}"
                    )
                if invalid_ids:
                    logger.warning("Final hypothesis contained invented IDs and was rejected: %s", invalid_ids)
                else:
                    logger.warning("Final synthesis did not contain four sufficiently detailed hypotheses; retrying.")
        except Exception as exc:
            logger.warning("Final hypothesis synthesis attempt %s failed: %s", attempt + 1, exc)
    return _build_fallback_narrative_report(user_msg, df, profile)


# ----- Follow-up Handlers ---------------------------------------------------

def answer_complaint_details(user_query: str, complaints: list, history: list = None, hypothesis: str = None) -> str:
    if not complaints:
        return "Не удалось найти информацию по запрошенным обращениям."

    formatted_texts = []
    for idx, comp in enumerate(complaints, 1):
        cid = comp.get("id", "N/A")
        desc = comp.get("desc", comp.get("short_description", "—"))
        dialogue = comp.get("dialogue", comp.get("description", "—"))
        date_str = comp.get("date", "—")
        if len(str(dialogue)) > 2000:
            dialogue = str(dialogue)[:2000] + "..."
        formatted_texts.append(
            f"--- ОБРАЩЕНИЕ #{idx} (ID: {cid}, Дата: {date_str}) ---\n"
            f"Короткое описание: {desc}\n"
            f"Транскрибация диалога: {dialogue}\n"
        )

    context_str = "\n".join(formatted_texts)
    system_prompt = """Ты — ведущий эксперт-аналитик Службы контроля качества Сбербанка.
Пользователь просит пояснить детали по конкретным обращениям. Отвечай точечно, ссылаясь на факты из текстов."""
    user_prompt = f"""Запрос пользователя: "{user_query}"

Детали обращений:
{context_str}

Дай подробный и точный ответ."""

    messages = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_prompt})

    ask_llm = _get_ask_llm_fn()
    res = ask_llm(messages)
    if res and len(str(res).strip()) > 0:
        return str(res)
    return context_str


def answer_complaint_follow_up(user_query: str, complaints: list, history: list = None, hypothesis: str = None, total_session_count: int = None) -> str:
    if not complaints:
        cnt_str = f"всех {total_session_count:,}" if total_session_count else "всех"
        return f"В базе выгрузки (из {cnt_str} обращений) не найдено обращений, содержащих информацию по запросу '{user_query}'."

    target_complaints = complaints[:50]
    formatted_texts = []
    for idx, comp in enumerate(target_complaints, 1):
        cid = comp.get("id", "N/A")
        desc = comp.get("desc", comp.get("short_description", "—"))
        dialogue = comp.get("dialogue", comp.get("description", "—"))
        date_str = comp.get("date", "—")
        dialogue_str = str(dialogue)
        if len(dialogue_str) > 1000:
            dialogue_str = dialogue_str[:1000] + "..."
        formatted_texts.append(
            f"- ID: {cid} | Дата: {date_str} | Описание: {desc}\n  Транскрибация: {dialogue_str}"
        )
    formatted_str = "\n".join(formatted_texts)

    system_prompt = """Ты — ведущий эксперт-аналитик Службы контроля качества Сбербанка.
Переданы semantic candidates, а не доказанные совпадения. Определи по их текстам, есть ли evidence для запроса. Все выводы подкрепляй ссылками на конкретные ID обращений."""
    user_prompt = f"""Запрос пользователя: "{user_query}"

Semantic candidates для проверки: {len(target_complaints)} из всего {total_session_count or len(complaints)} обращений в выгрузке.
{formatted_str}

Ответь на вопрос пользователя."""

    messages = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_prompt})

    ask_llm = _get_ask_llm_fn()
    res = ask_llm(messages)
    if res and len(str(res).strip()) > 0:
        return str(res)

    lines = [f"По запросу '{user_query}' отобрано {len(target_complaints)} semantic candidates для проверки:\n"]
    for idx, comp in enumerate(target_complaints[:10], 1):
        cid = comp.get("id", "N/A")
        desc = comp.get("desc", comp.get("short_description", "—"))
        date_str = comp.get("date", "—")
        lines.append(f"{idx}. Обращение #{cid} ({date_str}): {desc}")
    return "\n".join(lines)


def answer_complaint_dialog(user_query: str, history: list = None, hypothesis: str = None, total_count: int = None) -> str:
    system_prompt = """Ты — ведущий эксперт-аналитик Службы контроля качества и клиентского опыта Сбербанка.
Ты ведешь диалог с пользователем. Твои ответы должны основываться на данных текущей сессии и истории сообщений.
Если тебя просят пояснить понятие, термин, предыдущие выводы или спросят сколько всего обращений выгружено — дай точный и развернутый ответ."""

    if total_count and total_count > 0:
        system_prompt += f"\nВ текущей выгрузке обращений сессии содержится ровно {total_count:,} шт.\n"

    if hypothesis:
        system_prompt += f"\nДля контекста, ранее на основе всей выгрузки была сформирована следующая аналитическая гипотеза:\n{hypothesis}\n"

    messages = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_query})

    ask_llm = _get_ask_llm_fn()
    res = ask_llm(messages)
    if res and len(str(res).strip()) > 0:
        return str(res)

    cnt_info = f"В текущей выборке выгружено {total_count:,} обращений." if total_count else ""
    return f"Запрос по выгрузке: '{user_query}'. {cnt_info}"


def classify_complaint_intent(message: str) -> str:
    msg_lower = message.lower()
    search_keywords = ["в каких", "информация о", "найди", "покажи", "где говорится", "в каком", "список обращений", "поищи", "самые"]
    if any(k in msg_lower for k in search_keywords):
        return "search"
    return "chat"
