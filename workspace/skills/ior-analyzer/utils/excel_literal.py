"""Keep spreadsheet-looking text literal without changing numeric/date cells."""
from __future__ import annotations


def force_literal_excel_cells(worksheet) -> None:
    for row in worksheet.iter_rows():
        for cell in row:
            value = cell.value
            if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                cell.data_type = "s"
                cell.quotePrefix = True
