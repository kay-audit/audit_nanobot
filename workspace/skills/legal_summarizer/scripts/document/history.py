"""Offline Consultant+ history evidence; preserve fixed-width comparison columns.

This specialized DOCX adapter keeps spaces and hyperlinks' visible text, which
the generic text loader normalizes. It never follows relationships or URLs.
"""
from __future__ import annotations

import re
from datetime import datetime
from difflib import SequenceMatcher
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from document.history_archive import validate_archive

NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
DATE = r"\d{2}\.\d{2}\.\d{4}"
IDENTITY = re.compile(r"(?:Федеральный\s+закон|Федерального\s+закона)\s+от\s+(" + DATE + r")\s+[N№]\s*(\d+)-ФЗ", re.I)
REVISION = re.compile(r"^Редакция от (" + DATE + r")$")
SEPARATOR = re.compile(r"^\s*─{10,}\s*$")
MAX_DOCX_BYTES = 64 * 1024 * 1024


def compact(text: str) -> str:
    return " ".join(text.split())


def iso(value: str) -> str:
    return datetime.strptime(value, "%d.%m.%Y").date().isoformat()


def identity(text: str) -> tuple[str, str] | None:
    match = IDENTITY.search(compact(text))
    return (iso(match[1]), match[2]) if match else None


def read_lines(data: bytes) -> list[str]:
    if len(data) > MAX_DOCX_BYTES:
        raise ValueError("DOCX превышает лимит 64 MiB")
    with ZipFile(BytesIO(data)) as archive:
        validate_archive(archive)
        info = archive.getinfo("word/document.xml")
        if info.file_size > MAX_DOCX_BYTES:
            raise ValueError("XML документа превышает лимит 64 MiB")
        xml = archive.read(info)
    if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
        raise ValueError("DTD и XML entities не поддерживаются")
    body = ET.fromstring(xml).find(NS + "body")
    if body is None:
        raise ValueError("В DOCX отсутствует body")
    lines = []
    for block in body:
        text = ""
        for node in block.iter():
            if node.tag == NS + "t":
                text += node.text or ""
            elif node.tag == NS + "tab":
                text += "\t"
            elif node.tag in (NS + "br", NS + "cr"):
                text += "\n"
        lines.append(text.replace("\xa0", " "))
    return lines


def metadata(lines: list[str]) -> dict:
    # The first cover block names the document; later law citations are not IDs.
    cover = lines[0] if lines else ""
    ident = identity(cover)
    revision = re.search(r"ред\. от\s+(" + DATE + ")", cover, re.I)
    effective = re.search(r"вступ\. в силу с\s+(" + DATE + ")", cover, re.I)
    return {"identity": ident, "revision_date": iso(revision[1]) if revision else None,
            "effective_date": iso(effective[1]) if effective else None,
            "is_overview": compact(cover).lower().startswith("обзор изменений")}


def word_changes(before: str | None, after: str | None) -> list[dict]:
    if before is None or after is None:
        return []
    old, new = before.split(), after.split()
    return [{"before": " ".join(old[a:b]), "after": " ".join(new[c:d])}
            for tag, a, b, c, d in SequenceMatcher(None, old, new, autojunk=False).get_opcodes()
            if tag != "equal"]


def parse_overview(lines: list[str]) -> tuple[list[dict], list[str]]:
    events, warnings = [], []
    revision = effective = None
    basis = ""
    section = 0
    i = 0
    while i < len(lines):
        value = compact(lines[i])
        match = REVISION.fullmatch(value)
        if match:
            revision, effective, basis = iso(match[1]), None, ""
            section += 1
        elif value.startswith("(с изм."):
            match = re.search(r"вступ\. в силу с (" + DATE + ")", value)
            effective = iso(match[1]) if match else None
        elif value.startswith("Редакция подготовлена"):
            basis = value
        if not SEPARATOR.fullmatch(lines[i]):
            i += 1
            continue
        start = i + 1
        end = start
        while end < len(lines) and not SEPARATOR.fullmatch(lines[end]) and not REVISION.fullmatch(compact(lines[end])):
            end += 1
        block = [(n, s) for n, s in enumerate(lines[start:end], start) if s.strip()]
        if not block or block[0][1].strip() == "* * *":
            i += 1
            continue
        heading = compact(block[0][1])
        before = after = None
        missing = []
        header = next((j for j, (_, s) in enumerate(block) if "старая редакция" in s and "новая редакция" in s), None)
        if header is not None:
            # Consultant exports 35-character columns separated by five spaces.
            # Header indentation may shift by one; infer gutter from its labels.
            h = block[header][1]
            delta = h.index("новая редакция") - h.index("старая редакция")
            cut = h.index("старая редакция") - 9 + delta - 3
            rows = [s for _, s in block[header + 1:]]
            # Centered chapter headings use a one-character gutter at column 35.
            gutter = 2
            if heading.startswith("Наименование") and any(s[cut:cut + 2].strip() for s in rows):
                if rows and all(not s[35:36].strip() for s in rows):
                    cut, gutter = 35, 1
            if any("не приводится" in s for s in rows):
                missing.append("Обзор явно не приводит текст; внешняя ссылка не использовалась")
            elif delta != 38 or cut not in (35, 36, 37) or any(s[cut:cut + gutter].strip() for s in rows):
                missing.append("Нераспознанная разметка колонок; требуется чтение source_text")
            else:
                before = compact(" ".join(s[:cut] for s in rows)) or None
                after = compact(" ".join(s[cut:] for s in rows)) or None
        elif heading.startswith("Дополнение"):
            before = "Отсутствовало (структурная единица добавлена)"
            after = compact(" ".join(s for _, s in block[1:])) or None
        elif re.search(r"исключен|утратил[аио]? силу", heading, re.I):
            before = compact(" ".join(s for _, s in block[1:])) or None
            after = "Исключено"
        else:
            missing.append("Нераспознанный формат изменения; требуется чтение source_text")
        if before and "не приводится" in before:
            before = None
        if after and "не приводится" in after:
            after = None
        if before is None:
            missing.append("Старый текст не приведён или не распознан")
        if after is None:
            missing.append("Новый текст не приведён или не распознан")
        if revision is None:
            missing.append("Дата редакции не указана")
        if effective is None:
            missing.append("Дата вступления изменения в силу не указана отдельно")
        events.append({"id": f"change_{start:05d}", "section": section,
                       "revision_date": revision, "effective_date": effective,
                       "sort_date": effective or revision, "date_basis": "effective_date" if effective else "revision_date",
                       "provision": heading, "revision_basis": basis or None,
                       "before": before, "after": after, "text_changes": word_changes(before, after),
                       "missing": missing, "source_block_start": start + 1, "source_block_end": end,
                       "source_text": "\n".join(lines[start:end])})
        i = end
    events.sort(key=lambda e: (e["sort_date"] or "9999-12-31", e["revision_date"] or "", e["source_block_start"]))
    if not events:
        warnings.append("Обзор не содержит распознанных блоков изменений")
    warnings.append("Полнота истории ограничена приложенным обзором; ссылки не открывались")
    return events, warnings
