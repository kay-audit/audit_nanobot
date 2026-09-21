"""Direct loss SQL and vectorized financial/incident/month views."""
import pandas as pd

from preset_analysis.common import APPROVED_STATUSES, normalize_status
from .models import AnalysisData, AnalysisRequest

MAIN_COLUMNS = (
    "incdnt_id", "incdnt_sid", "incdnt_status_name", "risk_profile_id",
    "risk_profile_name", "org_struct_id", "org_struct_lvl_2_name",
    "org_struct_lvl_3_name", "process_lvl_4_name", "incdnt_type_lvl_1_name",
    "incdnt_type_lvl_2_name", "incdnt_summary_descr_txt", "incdnt_full_descr_txt",
)
FINANCIAL_COLUMNS = (
    "fin_impact_id", "fin_impact_sid", "fin_impact_type_name", "fin_impact_kind_name",
    "fin_impact_rub_amt", "fin_impact_creation_dttm", "fin_impact_detection_dt",
    "fin_impact_reg_dt", "fin_impact_monitoring_flag",
)


def aggregate_amounts(detail: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    grouped = detail.groupby(keys, dropna=False, sort=True).agg(
        direct_loss_rub=("fin_impact_rub_amt", "sum"),
        known_amount_count=("fin_impact_rub_amt", "count"),
        detail_count=("fin_impact_rub_amt", "size"),
    ).reset_index()
    grouped["amount_class"] = "zero"
    grouped.loc[grouped.known_amount_count.eq(0), "amount_class"] = "null/unknown"
    grouped.loc[grouped.direct_loss_rub.gt(0), "amount_class"] = "positive"
    return grouped


class DirectLossStrategy:
    def build_sql(self, request: AnalysisRequest, tables) -> str:
        # Dates are typed, validated ISO values, never interpolated raw input.
        columns = [f"ior.{c}" for c in MAIN_COLUMNS] + [f"fi.{c}" for c in FINANCIAL_COLUMNS]
        return ("SELECT " + ",\n       ".join(columns)
                + f"\nFROM {tables['ior']} ior\nINNER JOIN {tables['financial_impact']} fi"
                + " ON ior.incdnt_id = fi.incdnt_id\n"
                + "WHERE fi.fin_impact_type_name = 'Прямая потеря'\n"
                + f"AND fi.fin_impact_creation_dttm >= '{request.start.isoformat()}'\n"
                + f"AND fi.fin_impact_creation_dttm < '{request.end_exclusive.isoformat()}'")

    def prepare(self, raw: pd.DataFrame) -> AnalysisData:
        required = {"incdnt_id", "fin_impact_sid", "fin_impact_rub_amt", "fin_impact_creation_dttm", "incdnt_status_name"}
        if missing := required - set(raw.columns):
            raise ValueError("В выборке отсутствуют колонки: " + ", ".join(sorted(missing)))
        if raw.incdnt_id.isna().any():
            raise ValueError("Выборка содержит пустой canonical key incdnt_id.")
        keys = ["incdnt_id", "fin_impact_sid"]
        keyed = raw.fin_impact_sid.notna() & raw.fin_impact_sid.astype(str).str.strip().ne("")
        conflicts = raw.loc[keyed].groupby(keys, dropna=False).fin_impact_rub_amt.nunique(dropna=False).gt(1).sum()
        keep = ~keyed | ~raw.duplicated(keys, keep="first")
        detail = raw.loc[keep].copy()
        notes = []
        if conflicts:
            notes.append(f"Пар с конфликтующими суммами: {conflicts}; сохранена первая строка каждой пары.")
        if (~keyed).any():
            notes.append("Строки без fin_impact_sid сохранены: установить дубли по бизнес-ключу невозможно.")
        detail["amount_is_null"] = detail.fin_impact_rub_amt.isna()
        detail["fin_impact_rub_amt"] = pd.to_numeric(detail.fin_impact_rub_amt, errors="raise")
        detail["month"] = pd.to_datetime(detail.fin_impact_creation_dttm, format="ISO8601", errors="raise").dt.to_period("M").astype(str)
        context_cols = [c for c in MAIN_COLUMNS if c in detail]
        context = detail[context_cols].drop_duplicates("incdnt_id")
        incident = context.merge(aggregate_amounts(detail, ["incdnt_id"]), on="incdnt_id", validate="one_to_one")
        approved = incident.loc[incident.incdnt_status_name.map(normalize_status).isin(APPROVED_STATUSES)]
        approved_detail = detail.loc[detail.incdnt_id.isin(approved.incdnt_id)]
        monthly = aggregate_amounts(approved_detail, ["month", "incdnt_id"])
        return AnalysisData(detail, incident, approved_detail, approved, monthly, notes)
