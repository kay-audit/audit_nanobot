"""Deterministic report assembly and canonical session artifact storage."""
from __future__ import annotations

import json
from pathlib import Path


def label(value):
    return " ".join(str(value).split()) if value is not None else "Не указано"


def quote(value):
    if value is None:
        return "> Текст не приведён или не распознан."
    return "\n".join("> " + line for line in str(value).splitlines())


def render(result):
    lines = ["# История изменений", "", f"Исследуемый документ: {label(result['target'])}",
             f"Обзор: {label(result['overview'])}", f"Всего изменений: {result['total_changes']}",
             f"Пояснено: {result['explained_changes']}; требуют проверки: {result['needs_review']}",
             f"Статус: {result['status']}", ""]
    lines.extend("- " + label(w) for w in result.get("warnings", []))
    for n, event in enumerate(result["changes"], 1):
        lines.extend(["", f"## {n}. {label(event['sort_date'])} — {label(event['provision'])}", "",
                      f"**Дата редакции:** {label(event['revision_date'])}",
                      f"**Дата вступления в силу:** {label(event['effective_date'])}",
                      f"**Положение:** {label(event['provision'])}", "", "**Было:**", quote(event['before']),
                      "", "**Стало:**", quote(event['after']), "", "**Смысл изменения:**", event['meaning']])
        if event.get("needs_review"):
            lines.extend(["", "**Требует проверки:** " + label(event.get("reason"))])
        if event.get("missing"):
            lines.extend(["", "**Недостающие сведения:** " + "; ".join(event["missing"])])
        lines.extend(["", f"**Источник:** {label(result['overview'])}, блоки {event['source_block_start']}–{event['source_block_end']}"])
        if event.get("revision_basis"):
            lines.append("**Основание раздела обзора:** " + label(event["revision_basis"]))
    return "\n".join(lines) + "\n"


def persist(result, root: Path, session: str):
    from workspace.utils.session_file_store import SessionFileStore
    base = root / "workspace" / "data_store" / "cache" / "sessions" / session
    for path in (base, base / "results", base / "metadata.json"):
        if path.resolve() != path.absolute():
            raise ValueError("Перенаправленное хранилище результатов запрещено")
    # Same store/path contract as PostgresChannel and the runtime persistence hook.
    store = SessionFileStore(root / "workspace" / "data_store")
    report = store.save(session, render(result), "history_report", ext=".md", dedupe=True)
    machine = store.save(session, json.dumps(result, ensure_ascii=False, indent=2),
                         "history_result", ext=".json", dedupe=True)
    paths = ["data_store/" + item["path"] for item in (report, machine)]
    return {"result_path": paths[0], "result_json_path": paths[1], "media": paths}


def compact_result(result, artifacts):
    return {"status": result["status"], "mode": "doc_history", "operation_id": result["operation_id"],
            "target": Path(result["target"].split("::")[-1]).name,
            "overview": Path(result["overview"].split("::")[-1]).name,
            "total_changes": result["total_changes"], "explained_changes": result["explained_changes"],
            "needs_review": result["needs_review"], "warnings": [str(w)[:400] for w in result.get("warnings", [])[:5]],
            **artifacts, "message": "Полный отчёт сохранён. Передайте файлы из media пользователю; не читайте отчёт в контекст агента."}
