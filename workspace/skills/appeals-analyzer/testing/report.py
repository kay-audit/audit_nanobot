from __future__ import annotations


def source_provenance(dsn: str | None = None) -> str:
    """Доказательство, что отчёт построен на данных из PostgreSQL.

    Печатает схему, число строк по каждой таблице и выборку идентификаторов —
    по требованию PRT8 тестовые данные фичи обязаны жить в БД, и это должно
    быть видно в артефакте, а не утверждаться.
    """
    try:
        from .data_generator import TEST_SCHEMA, load_records, resolve_dsn, _connect
    except ImportError:  # pragma: no cover - прямой запуск модуля
        from data_generator import TEST_SCHEMA, load_records, resolve_dsn, _connect

    try:
        connection = _connect(dsn or resolve_dsn())
    except Exception as exc:
        return f"Источник данных недоступен: {exc}"

    try:
        counts = {}
        with connection.cursor() as cur:
            for table in ("appeals_structural", "appeal_body", "appeal_dialogs", "appeal_task"):
                cur.execute(f"SELECT count(*) FROM {TEST_SCHEMA}.{table}")
                counts[table] = cur.fetchone()[0]
            cur.execute(
                f"SELECT string_agg(app_row_id, ', ' ORDER BY app_row_id) "
                f"FROM (SELECT app_row_id FROM {TEST_SCHEMA}.appeals_structural "
                f"ORDER BY app_row_id LIMIT 3) t"
            )
            sample = cur.fetchone()[0] or "—"
    finally:
        connection.close()

    rows = "\n".join(f"  - {name}: {value}" for name, value in counts.items())
    return (
        f"## Источник данных\n"
        f"Схема PostgreSQL: `{TEST_SCHEMA}`\n\n"
        f"Загружено строк:\n{rows}\n\n"
        f"Первые идентификаторы: {sample}\n"
    )


def render_report(query: str, selected: list[dict], provenance: str | None = None) -> str:
    evidence = "\n".join(
        f"- **{row['appeal_id']}** ({row['date']}, {row['prd']} / {row['s_prd']}, {row['chnl']}), score={row['relevance_score']:.2f}: {row['text']} — {row['reason']}"
        for row in selected
    ) or "- Релевантных обращений не найдено."
    products = sorted({row["prd"] for row in selected})
    hypotheses = []
    if selected:
        evidence_ids = ", ".join(row["appeal_id"] for row in selected[:5])
        hypotheses = [
            f"Повторяемая тема «{products[0]}» требует проверки клиентского пути; evidence: {evidence_ids}.",
            f"Причины могут быть связаны с недостаточно понятным информированием клиента; evidence: {evidence_ids}.",
            f"Стоит сопоставить обращения с техническими событиями в те же даты; evidence: {evidence_ids}.",
        ]
    hypothesis_text = "\n".join(f"{i}. {text}" for i, text in enumerate(hypotheses, 1)) or "Гипотезы не сформированы без evidence."
    return (
        "# Тестовый отчёт по обращениям\n\n"
        f"Запрос: {query}\n\nНайдено обращений: **{len(selected)}**\n\n"
        "## Summary\nРезультат сформирован на синтетических данных после детерминированных фильтров и LLM relevance-проверки.\n\n"
        + (provenance if provenance is not None else source_provenance()) + "\n"
        "## Evidence\n" + evidence + "\n\n## Гипотезы\n" + hypothesis_text
    )
