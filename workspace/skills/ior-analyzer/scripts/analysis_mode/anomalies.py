"""Deterministic IQR, temporal shifts, and dimensional concentrations.

Four observations are the minimum for IQR, and a zero IQR is inconclusive.
Concentration (>=50%) and share shift (>=25 percentage points) are descriptive
screening rules, not statistical proof or causal findings.
"""
import pandas as pd

from .models import AnalysisData, AnalysisMetrics, AnomalyEvent
from .statistics import DIMENSIONS, dimension_totals

MIN_IQR_POINTS = 4
CONCENTRATION_SHARE = 0.5
SHARE_SHIFT = 0.25
# Monthly category shifts use percentage points: relative growth is unstable at zero.
CONCENTRATION_SHIFT_MIN_PP = 20.0
SIGNIFICANT_CONCENTRATION_SHARE = 0.30
LEADER_CHANGE_MIN_PP = 20.0
MATERIAL_MOM_CHANGE = 0.20
MATERIAL_AVERAGE_LOSS_CHANGE = 0.30
SHARE_DISPROPORTION_MIN_PP = 20.0
TREND_MIN_TOTAL_SHIFT_PP = 20.0
TREND_MIN_MONTHS = 3
MAX_EVENTS_PER_MONTH = 2

DIMENSION_LABELS = {"org": "оргструктура", "risk": "ЦПР", "process": "процесс"}


def _amount(value):
    return f"{float(value):,.2f}".replace(",", " ").replace(".", ",")


def _value(column, value):
    if column == "direct_loss_rub":
        return f"{_amount(value)} руб."
    if column == "unique_incidents":
        return f"{int(value):,}".replace(",", " ")
    return f"{float(value):.1%}"


def _category_title(row: dict, columns: tuple[str, ...]) -> str:
    return " / ".join("не указано" if pd.isna(row[column]) else str(row[column]) for column in columns)


def _selector(row: dict, columns: tuple[str, ...], months: list[str]) -> dict:
    return {"months": months, **{column: row[column] for column in columns}}


def _pct_change(before, after):
    return None if before == 0 else (after - before) / before


def _change_text(value):
    return "после нулевого значения" if value is None else f"на {value:+.1%}"


def iqr_bounds(values: pd.Series):
    if len(values) < MIN_IQR_POINTS:
        return None
    q1, q3 = values.quantile([0.25, 0.75])
    if q3 <= q1:
        return None
    return float(q1 - 1.5 * (q3 - q1)), float(q3 + 1.5 * (q3 - q1))


def detect_anomalies(data: AnalysisData, metrics: AnalysisMetrics) -> list[AnomalyEvent]:
    incidents = data.approved_incident_df
    if incidents.empty:
        return []
    events = []

    def add(kind, importance, description, facts, selector, **metadata):
        event = AnomalyEvent(f"signal-{len(events) + 1}", kind, importance, description, facts, selector, **metadata)
        related = select_event_incidents(data, event)
        event.evidence_incident_ids = related.nlargest(10, "direct_loss_rub").incdnt_id.tolist()
        events.append(event)

    positives = incidents.loc[incidents.direct_loss_rub.gt(0)]
    bounds = iqr_bounds(positives.direct_loss_rub)
    candidates = positives.loc[positives.direct_loss_rub.gt(bounds[1])] if bounds else positives.nlargest(3, "direct_loss_rub")
    for row in candidates.itertuples():
        sid = getattr(row, "incdnt_sid", row.incdnt_id)
        kind = "monetary_outlier" if bounds else "largest_observation"
        label = "Среди отдельных ИОР выделяется" if bounds else "Наибольшая прямая потеря приходится на"
        add(kind, 90 if bounds else 40, f"{label} {sid}: {_amount(row.direct_loss_rub)} руб.",
            {"amount": row.direct_loss_rub, "upper_bound": bounds[1] if bounds else None}, {"incdnt_id": row.incdnt_id})

    monthly = metrics.monthly
    labels = {"direct_loss_rub": "сумма прямых потерь", "unique_incidents": "количество ИОР",
              "positive_share": "доля ИОР с ненулевой суммой прямой потери",
              "zero_share": "доля ИОР с нулевой суммой прямой потери"}
    for column, label in labels.items():
        # Shares in empty months have no observations: exclude them from IQR.
        observed = monthly.loc[~monthly.is_partial_month]
        if column.endswith("share"):
            observed = observed.loc[observed.unique_incidents.gt(0)]
        bound = iqr_bounds(observed[column])
        if bound:
            outliers = observed.loc[observed[column].lt(bound[0]) | observed[column].gt(bound[1])]
            for row in outliers.to_dict("records"):
                value = row[column]
                add("monthly_outlier", 85, f"В {row['month']} {label} заметно отличается от остальных месяцев: {_value(column, value)}.",
                    {"metric": column, "value": value, "lower_bound": bound[0], "upper_bound": bound[1]}, {"months": [row["month"]]})
        previous = monthly[column].shift(1)
        delta = monthly[column] - previous
        for i in delta.dropna().loc[delta.ne(0)].index:
            if bool(monthly.loc[i - 1, "is_partial_month"]) or bool(monthly.loc[i, "is_partial_month"]):
                continue
            before, after, change = float(previous.loc[i]), float(monthly.loc[i, column]), float(delta.loc[i])
            months = [monthly.loc[i - 1, "month"], monthly.loc[i, "month"]]
            if column.endswith("share"):
                if not monthly.loc[[i - 1, i], "unique_incidents"].gt(0).all():
                    continue
                suffix = f"изменение {change * 100:+.1f} п.п."
                importance = 95 if abs(change) >= SHARE_SHIFT else 35
            else:
                suffix = "появление показателя после нуля" if before == 0 else f"изменение {change / before * 100:+.1f}%"
                importance = 65 if before == 0 or abs(change / before) >= 1 else 30
            add("share_shift" if column.endswith("share") else "month_change", importance,
                f"С {months[0]} по {months[1]} {label} изменилось: {_value(column, before)} → {_value(column, after)}; {suffix}.",
                {"metric": column, "before": before, "after": after, "delta": change}, {"months": months})

    # Joint frequency/severity movement is more informative than isolated MoM facts.
    for index in range(1, len(monthly)):
        previous, current = monthly.iloc[index - 1], monthly.iloc[index]
        if previous.is_partial_month or current.is_partial_month:
            continue
        count_change = _pct_change(float(previous.unique_incidents), float(current.unique_incidents))
        loss_change = _pct_change(float(previous.direct_loss_rub), float(current.direct_loss_rub))
        average_change = _pct_change(float(previous.average_loss_per_incident), float(current.average_loss_per_incident))
        if count_change is None or loss_change is None:
            continue
        opposite = count_change * loss_change < 0 and abs(count_change) >= MATERIAL_MOM_CHANGE and abs(loss_change) >= MATERIAL_MOM_CHANGE
        joint = count_change * loss_change > 0 and abs(count_change) >= MATERIAL_MOM_CHANGE and abs(loss_change) >= MATERIAL_MOM_CHANGE and average_change is not None and abs(average_change) >= MATERIAL_AVERAGE_LOSS_CHANGE
        if not (opposite or joint):
            continue
        interpretation = ("рост числа менее крупных по сумме событий" if count_change > 0 and average_change is not None and average_change < 0
                          else "снижение числа событий при росте их средней денежной тяжести" if count_change < 0 and average_change is not None and average_change > 0
                          else "существенное одновременное изменение частоты и денежной тяжести событий")
        previous_count = f"{int(previous.unique_incidents):,}".replace(",", " ")
        current_count = f"{int(current.unique_incidents):,}".replace(",", " ")
        description = (
            f"С {previous.month} по {current.month} количество ИОР изменилось с {previous_count} "
            f"до {current_count} ({count_change:+.1%}), а сумма прямых потерь — с "
            f"{_amount(previous.direct_loss_rub)} до {_amount(current.direct_loss_rub)} руб. ({loss_change:+.1%}). "
            f"Средняя потеря на ИОР изменилась с {_amount(previous.average_loss_per_incident)} до "
            f"{_amount(current.average_loss_per_incident)} руб. ({_change_text(average_change)}); это указывает на {interpretation}."
        )
        add("frequency_severity_divergence", 99, description, {
            "previous_month": previous.month, "current_month": current.month,
            "previous_incident_count": int(previous.unique_incidents), "current_incident_count": int(current.unique_incidents),
            "incident_count_change_pct": count_change, "previous_loss": float(previous.direct_loss_rub),
            "current_loss": float(current.direct_loss_rub), "loss_change_pct": loss_change,
            "previous_average_loss": float(previous.average_loss_per_incident),
            "current_average_loss": float(current.average_loss_per_incident), "average_loss_change_pct": average_change,
        }, {"months": [previous.month, current.month]}, month=current.month, previous_month=previous.month, severity="high")

    for dimension, columns in DIMENSIONS.items():
        concentration = metrics.monthly_concentrations.get(dimension, pd.DataFrame())
        if concentration.empty:
            continue
        label = DIMENSION_LABELS[dimension]
        for _, category_rows in concentration.groupby(list(columns), dropna=False, sort=False):
            category_rows = category_rows.sort_values("month", kind="stable").reset_index(drop=True)
            # Maximal full-month runs for persistent dominance and monotonic trends.
            records = category_rows.to_dict("records")
            runs, run = [], []
            for record in records + [None]:
                if record is not None and not record["is_partial_month"]:
                    run.append(record)
                else:
                    if run: runs.append(run)
                    run = []
            for full_run in runs:
                dominant = []
                for record in full_run + [None]:
                    if record is not None and record["share_of_month_loss"] >= CONCENTRATION_SHARE:
                        dominant.append(record)
                    else:
                        if len(dominant) >= TREND_MIN_MONTHS:
                            category = _category_title(dominant[0], columns)
                            shares = [float(row["share_of_month_loss"]) for row in dominant]
                            months = [row["month"] for row in dominant]
                            add("persistent_concentration", 97,
                                f"Категория «{category}» ({label}) сохраняла доминирующую долю прямых потерь "
                                f"{len(months)} месяца подряд — от {min(shares):.1%} до {max(shares):.1%} ({months[0]}–{months[-1]}).",
                                {"dimension": dimension, "value": category, "months": months, "shares": shares},
                                _selector(dominant[-1], columns, months), dimension=dimension, category=category,
                                month=months[-1], previous_month=months[0], severity="high")
                        dominant = []
                trend = [full_run[0]] if full_run else []
                direction = 0
                for record in full_run[1:] + [None]:
                    new_direction = 0 if record is None else (1 if record["share_of_month_loss"] > trend[-1]["share_of_month_loss"] else -1 if record["share_of_month_loss"] < trend[-1]["share_of_month_loss"] else 0)
                    if record is not None and new_direction and (direction in (0, new_direction)):
                        trend.append(record); direction = new_direction
                        continue
                    if len(trend) >= TREND_MIN_MONTHS:
                        shift = (trend[-1]["share_of_month_loss"] - trend[0]["share_of_month_loss"]) * 100
                        if abs(shift) >= TREND_MIN_TOTAL_SHIFT_PP:
                            category = _category_title(trend[0], columns)
                            months = [row["month"] for row in trend]
                            add("multi_month_trend", 95, f"Доля категории «{category}» ({label}) в сумме прямых потерь "
                                f"{'росла' if shift > 0 else 'снижалась'} {len(months)} месяца подряд — с "
                                f"{trend[0]['share_of_month_loss']:.1%} до {trend[-1]['share_of_month_loss']:.1%} ({shift:+.1f} п.п.).",
                                {"dimension": dimension, "value": category, "months": months, "total_shift_pp": shift},
                                _selector(trend[-1], columns, months), dimension=dimension, category=category,
                                month=months[-1], previous_month=months[0], severity="high")
                    trend = [trend[-1], record] if record is not None and new_direction else ([record] if record else [])
                    direction = new_direction
            for index in range(1, len(category_rows)):
                previous = category_rows.iloc[index - 1].to_dict()
                current = category_rows.iloc[index].to_dict()
                if previous["is_partial_month"] or current["is_partial_month"]:
                    continue
                previous_share = float(previous["share_of_month_loss"])
                current_share = float(current["share_of_month_loss"])
                delta_pp = round((current_share - previous_share) * 100, 10)
                if abs(delta_pp) < CONCENTRATION_SHIFT_MIN_PP or max(previous_share, current_share) < SIGNIFICANT_CONCENTRATION_SHARE:
                    continue
                category = _category_title(current, columns)
                facts = {
                    "dimension": dimension, "value": category,
                    "previous_month": previous["month"], "current_month": current["month"],
                    "previous_share": previous_share, "current_share": current_share,
                    "delta_percentage_points": delta_pp,
                    "previous_loss": float(previous["direct_loss_sum"]), "current_loss": float(current["direct_loss_sum"]),
                    "previous_incident_count": int(previous["unique_incidents"]),
                    "current_incident_count": int(current["unique_incidents"]),
                    "previous_incident_share": float(previous["share_of_month_incidents"]),
                    "current_incident_share": float(current["share_of_month_incidents"]),
                }
                if previous_share == 0 and current_share >= SIGNIFICANT_CONCENTRATION_SHARE:
                    kind = "new_concentration"
                    wording = "возникла новая значимая концентрация"
                    importance = 88 + min(abs(delta_pp), 60) / 10
                elif delta_pp < 0:
                    kind = "concentration_drop"
                    wording = "доля снизилась"
                    importance = 80 + min(abs(delta_pp), 60) / 10
                else:
                    kind = "concentration_shift"
                    wording = "доля выросла"
                    importance = 82 + min(abs(delta_pp), 60) / 10
                count_wording = {"org": "Количество ИОР по этой оргструктуре",
                                 "risk": "Количество ИОР по этому ЦПР",
                                 "process": "Количество ИОР в этом процессе"}[dimension]
                description = (f"В {current['month']} по категории «{category}» ({label}) {wording}: "
                               f"с {previous_share:.1%} до {current_share:.1%} ({delta_pp:+.1f} п.п.). "
                               f"{count_wording}: {int(previous['unique_incidents'])} → {int(current['unique_incidents'])}.")
                add(kind, importance, description, facts, _selector(current, columns, [previous["month"], current["month"]]),
                    dimension=dimension, category=category, month=current["month"], previous_month=previous["month"],
                    severity="high" if abs(delta_pp) >= 40 else "medium")

        # Leader changes are evaluated independently from per-category shifts.
        leaders = []
        for month, month_rows in concentration.groupby("month", sort=True):
            month_rows = month_rows.sort_values(["share_of_month_loss", "direct_loss_sum"], ascending=False, kind="stable")
            leaders.append(month_rows.iloc[0].to_dict())
        for index in range(1, len(leaders)):
            previous, current = leaders[index - 1], leaders[index]
            if previous["is_partial_month"] or current["is_partial_month"]:
                continue
            if previous["month_direct_loss_sum"] == 0 or current["month_direct_loss_sum"] == 0:
                continue
            previous_category = _category_title(previous, columns)
            current_category = _category_title(current, columns)
            if previous_category == current_category:
                continue
            previous_rows = concentration.loc[concentration.month.eq(previous["month"])]
            current_rows = concentration.loc[concentration.month.eq(current["month"])]
            previous_new = previous_rows
            current_old = current_rows
            for column in columns:
                previous_new = previous_new.loc[previous_new[column].isna() if pd.isna(current[column]) else previous_new[column].eq(current[column])]
                current_old = current_old.loc[current_old[column].isna() if pd.isna(previous[column]) else current_old[column].eq(previous[column])]
            new_previous_share = float(previous_new.share_of_month_loss.iloc[0]) if not previous_new.empty else 0.0
            old_current_share = float(current_old.share_of_month_loss.iloc[0]) if not current_old.empty else 0.0
            transition_pp = round(max(
                (float(current["share_of_month_loss"]) - new_previous_share) * 100,
                (float(previous["share_of_month_loss"]) - old_current_share) * 100,
            ), 10)
            if transition_pp < LEADER_CHANGE_MIN_PP or max(previous["share_of_month_loss"], current["share_of_month_loss"]) < SIGNIFICANT_CONCENTRATION_SHARE:
                continue
            facts = {
                "dimension": dimension, "previous_month": previous["month"], "current_month": current["month"],
                "previous_leader": previous_category, "current_leader": current_category,
                "previous_leader_share": float(previous["share_of_month_loss"]),
                "current_leader_share": float(current["share_of_month_loss"]),
                "transition_strength_percentage_points": transition_pp,
                "current_leader_loss": float(current["direct_loss_sum"]),
                "current_leader_incident_count": int(current["unique_incidents"]),
            }
            add("leader_change", 90 + min(transition_pp, 50) / 10,
                f"В {current['month']} лидером по сумме прямых потерь среди категорий «{label}» стала "
                f"«{current_category}» вместо «{previous_category}»; её доля составила "
                f"{float(current['share_of_month_loss']):.1%}.",
                facts, _selector(current, columns, [previous["month"], current["month"]]),
                dimension=dimension, category=current_category, month=current["month"], previous_month=previous["month"],
                severity="high" if transition_pp >= 40 else "medium")

    for dimension, columns in DIMENSIONS.items():
        if not all(c in incidents for c in columns):
            continue
        rows = dimension_totals(incidents, columns)
        for row in rows.to_dict("records"):
            gap_pp = (row["share"] - row["count_share"]) * 100
            if abs(gap_pp) < SHARE_DISPROPORTION_MIN_PP or max(row["share"], row["count_share"]) < SIGNIFICANT_CONCENTRATION_SHARE:
                continue
            title = _category_title(row, columns)
            if gap_pp > 0:
                text = (f"На категорию «{title}» ({DIMENSION_LABELS[dimension]}) приходится {row['share']:.1%} суммы "
                        f"прямых потерь при доле {row['count_share']:.1%} ИОР. Денежные потери сконцентрированы "
                        "в сравнительно небольшой группе событий.")
            else:
                text = (f"На категорию «{title}» ({DIMENSION_LABELS[dimension]}) приходится {row['count_share']:.1%} ИОР, "
                        f"но {row['share']:.1%} суммы прямых потерь. Это указывает на высокую частоту сравнительно "
                        "менее крупных по сумме событий.")
            add("share_disproportion", 96 + min(abs(gap_pp), 60) / 10, text,
                {"dimension": dimension, "category": title, "loss_share": row["share"],
                 "incident_share": row["count_share"], "gap_pp": gap_pp,
                 "direct_loss": row["direct_loss_rub"], "unique_incidents": row["unique_incidents"]},
                {column: row[column] for column in columns}, dimension=dimension, category=title, severity="high")
        for row in rows.loc[rows.share.ge(CONCENTRATION_SHARE) | rows.count_share.ge(CONCENTRATION_SHARE)].to_dict("records"):
            selector = {c: row[c] for c in columns}
            title = _category_title(row, columns)
            add("concentration", 75, f"На категорию «{title}» ({DIMENSION_LABELS[dimension]}) приходится "
                f"{row['share']:.1%} суммы прямых потерь и {row['count_share']:.1%} количества ИОР.",
                {k: row[k] for k in ("share", "count_share", "direct_loss_rub", "unique_incidents")}, selector,
                dimension=dimension, category=title, severity="medium")
    # A no-positive population still provides real, explicitly non-statistical cases.
    if not events:
        row = incidents.iloc[0]
        add("case_observation", 10, "Доступны только отдельные случаи; устойчивые аномалии не установлены.",
            {"amount_class": row.amount_class}, {"incdnt_id": row.incdnt_id})
    return sorted(events, key=lambda event: (-event.importance, event.event_id))


def select_event_incidents(data: AnalysisData, event: AnomalyEvent) -> pd.DataFrame:
    rows = data.approved_incident_df
    for key, value in event.selector.items():
        if key == "months":
            ids = data.monthly_incident_df.loc[data.monthly_incident_df.month.isin(value), "incdnt_id"]
            rows = rows.loc[rows.incdnt_id.isin(ids)]
        else:
            rows = rows.loc[rows[key].isna() if pd.isna(value) else rows[key].eq(value)]
    return rows


def report_events(events: list[AnomalyEvent], maximum: int = 8) -> list[AnomalyEvent]:
    """Deduplicate dimension/month siblings, then round-robin signal families."""
    divergence_pairs = {tuple(e.selector.get("months", [])) for e in events if e.kind == "frequency_severity_divergence"}
    deduplicated, seen = [], set()
    for event in events:
        # Plain period concentrations remain available to Qwen but add little to
        # the user list without a material frequency/severity disproportion.
        if event.kind == "concentration":
            continue
        if event.kind == "month_change" and tuple(event.selector.get("months", [])) in divergence_pairs:
            continue
        if event.kind in {"concentration_shift", "new_concentration", "concentration_drop", "leader_change"}:
            key = ("monthly_concentration", event.dimension, event.month)
        else:
            family = "temporal" if event.kind in {"share_shift", "month_change", "monthly_outlier"} else event.kind
            key = (family, repr(event.selector))
        if key not in seen:
            seen.add(key)
            deduplicated.append(event)
    buckets = {}
    for event in deduplicated:
        if event.kind in {"concentration_shift", "new_concentration", "concentration_drop", "leader_change"}:
            family = f"monthly_concentration:{event.dimension}"
        elif event.kind in {"share_shift", "month_change", "monthly_outlier"}:
            family = "temporal"
        elif event.kind == "concentration":
            family = f"period_concentration:{event.dimension}"
        else:
            family = event.kind
        buckets.setdefault(family, []).append(event)
    selected, month_counts = [], {}
    deferred = []
    while buckets and len(selected) < maximum:
        for family in list(buckets):
            event = buckets[family].pop(0)
            month = event.month or (event.selector.get("months") or [None])[-1]
            if month and month_counts.get(month, 0) >= MAX_EVENTS_PER_MONTH:
                deferred.append(event)
            else:
                selected.append(event)
                if month: month_counts[month] = month_counts.get(month, 0) + 1
            if not buckets[family]:
                del buckets[family]
            if len(selected) == maximum:
                break
    if len(selected) < maximum:
        remaining = deferred + [event for rows in buckets.values() for event in rows]
        selected.extend(remaining[:maximum - len(selected)])
    return selected
