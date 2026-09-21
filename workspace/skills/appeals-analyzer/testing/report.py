from __future__ import annotations


def render_report(query: str, selected: list[dict]) -> str:
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
        "## Evidence\n" + evidence + "\n\n## Гипотезы\n" + hypothesis_text
    )
