"""Opt-in XLSX only; preserve all statuses and unique financial detail rows."""
from pathlib import Path
from uuid import uuid4

import pandas as pd

from .direct_loss import MAIN_COLUMNS, FINANCIAL_COLUMNS

GENERATED_FILES = Path(__file__).resolve().parents[4] / "data_store" / "generated_files"
EXCEL_ROWS_PER_SHEET = 1_048_575  # Excel's row capacity less the header.


def export_details(detail: pd.DataFrame, output_dir: Path | None = None) -> Path:
    from utils.dataframe_ops import prepare_df_for_excel

    output_dir = Path(output_dir) if output_dir is not None else GENERATED_FILES
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"ior_analysis_{uuid4().hex}.xlsx"
    columns = [c for c in (*MAIN_COLUMNS, *FINANCIAL_COLUMNS, "amount_is_null") if c in detail]
    prepared = prepare_df_for_excel(detail[columns])
    try:
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            # Partition only the export, never truncate the analytical population.
            for sheet, offset in enumerate(range(0, len(prepared), EXCEL_ROWS_PER_SHEET), 1):
                prepared.iloc[offset:offset + EXCEL_ROWS_PER_SHEET].to_excel(writer, sheet_name=f"Последствия_{sheet}", index=False)
                ws = writer.sheets[f"Последствия_{sheet}"]
                ws.freeze_panes = "A2"
                ws.auto_filter.ref = ws.dimensions
                # Export descriptions as literal text, including leading '='.
                for row in ws.iter_rows():
                    for cell in row:
                        if cell.data_type == "f":
                            cell.data_type = "s"
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path
