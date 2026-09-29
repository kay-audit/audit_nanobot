"""All statistics use the full relevant population before presentation limits."""
import calendar
from datetime import timedelta

import pandas as pd

from .models import AnalysisData, AnalysisMetrics, AnalysisRequest

DIMENSIONS = {
    "org": ("org_struct_lvl_3_name",),
    "risk": ("risk_profile_id", "risk_profile_name"),
    "process": ("process_lvl_4_name",),
}
BOUNDARY_MONTH_TOLERANCE_DAYS = 2


def month_partial_flags(request: AnalysisRequest, months: pd.Index) -> pd.Series:
    """Mark boundary months missing more than the two-day business tolerance."""
    flags = pd.Series(False, index=months, dtype=bool)
    if flags.empty:
        return flags
    start_month = request.start.strftime("%Y-%m")
    end_month = request.end.strftime("%Y-%m")
    first_day = request.start.replace(day=1)
    last_day = request.end.replace(day=calendar.monthrange(request.end.year, request.end.month)[1])
    if start_month in flags.index and request.start > first_day + timedelta(days=BOUNDARY_MONTH_TOLERANCE_DAYS):
        flags.loc[start_month] = True
    if end_month in flags.index and request.end < last_day - timedelta(days=BOUNDARY_MONTH_TOLERANCE_DAYS):
        flags.loc[end_month] = True
    return flags


def dimension_totals(incidents: pd.DataFrame, columns: tuple[str, ...]) -> pd.DataFrame:
    rows = incidents.groupby(list(columns), dropna=False).agg(
        unique_incidents=("incdnt_id", "nunique"), direct_loss_rub=("direct_loss_rub", "sum")
    ).reset_index()
    total = incidents.direct_loss_rub.sum()
    rows["share"] = rows.direct_loss_rub / total if total else 0.0
    rows["count_share"] = rows.unique_incidents / len(incidents) if len(incidents) else 0.0
    return rows.sort_values(["direct_loss_rub", "unique_incidents"], ascending=False, kind="stable")


def monthly_dimension_totals(
    data: AnalysisData,
    monthly: pd.DataFrame,
    columns: tuple[str, ...],
) -> pd.DataFrame:
    """Return a complete month/category grid with safe within-month shares."""
    if data.monthly_incident_df.empty or not all(column in data.approved_incident_df for column in columns):
        return pd.DataFrame()
    context = data.approved_incident_df[["incdnt_id", *columns]].drop_duplicates("incdnt_id")
    rows = data.monthly_incident_df.merge(context, on="incdnt_id", validate="many_to_one")
    grouped = rows.groupby(["month", *columns], dropna=False).agg(
        unique_incidents=("incdnt_id", "nunique"),
        direct_loss_sum=("direct_loss_rub", "sum"),
    ).reset_index()
    categories = grouped[list(columns)].drop_duplicates()
    month_frame = monthly[["month", "unique_incidents", "direct_loss_rub", "is_partial_month"]].rename(
        columns={"unique_incidents": "month_unique_incidents", "direct_loss_rub": "month_direct_loss_sum"}
    )
    grid = month_frame.assign(_key=1).merge(categories.assign(_key=1), on="_key").drop(columns="_key")
    result = grid.merge(grouped, on=["month", *columns], how="left")
    result[["unique_incidents", "direct_loss_sum"]] = result[["unique_incidents", "direct_loss_sum"]].fillna(0)
    result["unique_incidents"] = result.unique_incidents.astype(int)
    result["share_of_month_loss"] = 0.0
    result["share_of_month_incidents"] = 0.0
    loss_mask = result.month_direct_loss_sum.ne(0)
    count_mask = result.month_unique_incidents.ne(0)
    result.loc[loss_mask, "share_of_month_loss"] = (
        result.loc[loss_mask, "direct_loss_sum"] / result.loc[loss_mask, "month_direct_loss_sum"]
    )
    result.loc[count_mask, "share_of_month_incidents"] = (
        result.loc[count_mask, "unique_incidents"] / result.loc[count_mask, "month_unique_incidents"]
    )
    return result.sort_values(["month", "direct_loss_sum", *columns], ascending=[True, False, *([True] * len(columns))], kind="stable")


def calculate_metrics(data: AnalysisData, request: AnalysisRequest) -> AnalysisMetrics:
    approved = data.approved_incident_df
    top = {}
    for name in ("org", "risk"):
        cols = DIMENSIONS[name]
        top[name] = (dimension_totals(approved, cols).head(10)
                     if all(c in approved for c in cols) else pd.DataFrame())
    statuses = data.incident_df.groupby("incdnt_status_name", dropna=False).incdnt_id.nunique().reset_index(name="unique_incidents")
    monthly = pd.DataFrame()
    if not approved.empty:
        mi = data.monthly_incident_df
        monthly = mi.groupby("month").agg(
            unique_incidents=("incdnt_id", "nunique"), direct_loss_rub=("direct_loss_rub", "sum")
        )
        classes = pd.crosstab(mi.month, mi.amount_class)
        for column, label in (("positive", "positive"), ("zero", "zero"), ("null_unknown", "null/unknown")):
            monthly[column] = classes[label] if label in classes else 0
        index = pd.period_range(request.start, request.end, freq="M").astype(str)
        monthly = monthly.reindex(index, fill_value=0).rename_axis("month")
        denominator_count = monthly.unique_incidents.replace(0, float("nan"))
        monthly["average_loss_per_incident"] = (
            monthly.direct_loss_rub / denominator_count
        ).fillna(0.0)
        denominator = denominator_count
        for label in ("positive", "zero"):
            monthly[f"{label}_share"] = (monthly[label] / denominator).fillna(0)
        monthly = monthly.reset_index()
        partial = month_partial_flags(request, pd.Index(monthly.month))
        monthly["is_partial_month"] = monthly.month.map(partial).astype(bool)
    monthly_concentrations = {
        name: monthly_dimension_totals(data, monthly, columns)
        for name, columns in DIMENSIONS.items()
    } if not monthly.empty else {}
    return AnalysisMetrics(len(data.incident_df), float(data.incident_df.direct_loss_rub.sum()),
                           float(approved.direct_loss_rub.sum()), statuses, top["org"], top["risk"], monthly,
                           monthly_concentrations)
