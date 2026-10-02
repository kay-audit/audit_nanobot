from __future__ import annotations

import hashlib
from pathlib import Path

from wiki_agent.config import Settings


def create_workspace(root: Path) -> None:
    for directory in (
        "schema/operations",
        "wiki/pages",
        "wiki/sources",
        "raw/sources",
        "raw/extracted",
        "proposals",
        "reports/lint",
    ):
        (root / directory).mkdir(parents=True, exist_ok=True)

    write(root, "AGENTS.md", "# Правила\n\nНе изменяй raw/sources.\n")
    write(root, "schema/operations/query.md", "# Query\n\nТолько чтение.\n")
    write(root, "schema/operations/lint.md", "# Lint\n\nТолько отчёт.\n")
    write(
        root,
        "schema/operations/ingest.md",
        "# Ingest\n\nСначала Proposal.\n",
    )
    write(root, "schema/taxonomy.md", "# Таксономия\n")
    write(
        root,
        "wiki/index.md",
        "# Индекс\n\n- [[Topic]]\n- [[Source]]\n",
    )
    write(root, "wiki/log.md", "# Журнал изменений\n")
    write(root, "wiki/pages/Topic.md", topic_content())
    write(root, "wiki/sources/Source.md", source_card_content())
    write(root, "raw/sources/source.md", "# Source\n\nVerified fact.\n")
    write(
        root,
        "raw/sources/new.md",
        "# New source\n\nNew verified fact.\n",
    )


def settings(root: Path) -> Settings:
    return Settings(
        root=root.resolve(),
        provider="stub",
        max_file_chars=100_000,
        max_context_chars=50_000,
        max_query_pages=5,
        max_hops=3,
        query_search="lexical",
    )


def topic_content() -> str:
    return """---
title: Topic
category: concepts
aliases: []
tags:
  - test
status: active
updated_at: 2026-07-25
---

# Topic

## Краткое описание

Verified fact from [[Source]].

## Связанные страницы

- [[Source]] — source.

## Противоречия и неопределённости

Не выявлены.

## Источники

- [[Source]]
"""


def source_card_content() -> str:
    return """---
title: Source
category: sources
source_path: raw/sources/source.md
status: processed
updated_at: 2026-07-25
---

# Source

## Краткое содержание

Test source.

## Основные темы

- [[Topic]]

## Связанные страницы

- [[Topic]] — integration.

## Ограничения чтения

Нет.

## Текстовое представление

Оригинальный Markdown.

## Исходный файл

`raw/sources/source.md`
"""


def valid_knowledge_json(root: Path) -> str:
    import json

    del root
    return json.dumps(
        {
            "protocol": "knowledge-v1",
            "summary": "Точечно добавить новый подтверждённый факт.",
            "source_title": "New Source",
            "source_summary": "New test source.",
            "source_limitations": [],
            "conflicts": [],
            "topics": [
                {
                    "title": "Topic",
                    "summary": "Самостоятельная полезная тема.",
                    "claims": ["New verified fact."],
                    "aliases": [],
                    "tags": ["test"],
                    "category": "concepts",
                    "index_section": "Тестовые знания",
                    "related_topics": [],
                    "limitations": [],
                }
            ],
        },
        ensure_ascii=False,
    )


def write(root: Path, relative: str, content: str) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def tree_digest(root: Path, *, exclude: tuple[str, ...] = ()) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if any(
            relative == item or relative.startswith(f"{item}/")
            for item in exclude
        ):
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()
