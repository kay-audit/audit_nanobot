"""Typed boundaries for the structured analysis pipeline."""
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import pandas as pd


class AnalysisRequestError(ValueError):
    """Invalid structured input; must be raised before any external call."""


@dataclass(frozen=True)
class AnalysisRequest:
    money_filter: str
    start: date
    end: date
    export_excel: bool = False
    action: str = "Анализ"
    org_filter: None = None

    @property
    def end_exclusive(self) -> date:
        return self.end + timedelta(days=1)


@dataclass
class AnalysisData:
    detail_df: pd.DataFrame
    incident_df: pd.DataFrame
    approved_detail_df: pd.DataFrame
    approved_incident_df: pd.DataFrame
    monthly_incident_df: pd.DataFrame
    quality_notes: list[str] = field(default_factory=list)


@dataclass
class AnalysisMetrics:
    unique_incidents: int
    total_loss: float
    approved_loss: float
    statuses: pd.DataFrame
    top_org: pd.DataFrame
    top_risk: pd.DataFrame
    monthly: pd.DataFrame
    monthly_concentrations: dict[str, pd.DataFrame] = field(default_factory=dict)


@dataclass
class AnomalyEvent:
    event_id: str
    kind: str
    importance: float
    description: str
    facts: dict[str, Any]
    # A selector binds an event to real approved incidents without copying raw rows.
    selector: dict[str, Any] = field(default_factory=dict)
    dimension: str | None = None
    category: str | None = None
    month: str | None = None
    previous_month: str | None = None
    severity: str = "low"
    evidence_incident_ids: list[Any] = field(default_factory=list)
