"""Orchestration without legacy sessions, presets, charts or export cards."""
import asyncio

from .anomalies import detect_anomalies
from .export import export_details
from .hypotheses import build_evidence_pack, generate_hypotheses
from .models import AnalysisRequest
from .registry import MONEY_FILTERS
from .report import render_report
from .statistics import calculate_metrics


async def run_analysis_mode(request: AnalysisRequest, store, *, ask=None, output_dir=None) -> str:
    strategy = MONEY_FILTERS[request.money_filter]
    raw = await asyncio.to_thread(store.query_sql, strategy.build_sql(request, store.tables))
    if raw.empty:
        return f"За период {request.start}–{request.end} ИОР с прямыми потерями не найдено."
    data = strategy.prepare(raw)
    metrics = calculate_metrics(data, request)
    events = detect_anomalies(data, metrics)
    hypotheses = ""
    if not data.approved_incident_df.empty:
        pack = build_evidence_pack(data, metrics, events, request)
        hypotheses = await generate_hypotheses(pack, ask=ask)
    result = render_report(request, data, metrics, events, hypotheses)
    if request.export_excel:
        path = await asyncio.to_thread(export_details, data.detail_df, output_dir)
        # Delivery is handled by Nanobot media, not a local Markdown link.
    return result
