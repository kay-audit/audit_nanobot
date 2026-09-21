"""Автономные тесты гранулярности пресета vozmeshenie_ior."""
from __future__ import annotations

import unittest

import pandas as pd

from ior_hypothesis import (
    build_deterministic_full_report,
    profile_vozmeshenie_dataframe,
    sanitize_vozmeshenie_narrative,
)
from vozmeshenie_analysis import format_vozmeshenie_header, prepare_vozmeshenie_views


def _sample_df() -> pd.DataFrame:
    return pd.DataFrame({
        "incdnt_sid": ["EVE-1", "EVE-1", "EVE-2"],
        "recovery_sid": ["EVE-1-R1", "EVE-1-R2", "EVE-2-R1"],
        "recovery_rub_amt": [100.0, 200.0, 50.0],
        "recovery_type_name": ["Компенсация от клиента", "Страховая выплата", "Компенсация от клиента"],
        "incdnt_status_name": ["УТВЕРЖДЁН", "УТВЕРЖДЁН", "УДАЛЁН"],
        "org_struct_lvl_3_name": ["Московский банк", "Московский банк", "Уральский банк"],
        "process_lvl_4_name": ["П1001", "П1001", "П2002"],
        "recovery_reg_dt": ["2025-03-01", "2025-03-02", "2025-04-01"],
        # Поле присутствует в основной таблице, но не должно попадать в анализ возмещений.
        "incdnt_sum": [999999.0, 999999.0, 777777.0],
        "incdnt_full_descr_txt": ["Первый ИОР", "Первый ИОР", "Второй ИОР"],
    })


class VozmeshenieAggregationTests(unittest.TestCase):
    def test_preserves_operation_sum_and_deduplicates_incidents(self):
        raw = _sample_df()
        incidents, metrics = prepare_vozmeshenie_views(raw)

        self.assertEqual(len(raw), 3)
        self.assertEqual(metrics["total_rows"], 3)
        self.assertEqual(metrics["unique_incidents"], 2)
        self.assertEqual(metrics["total_recovery"], 350.0)
        self.assertEqual(len(incidents), 2)

        amounts = incidents.set_index("incdnt_sid")["recovery_rub_amt"].to_dict()
        self.assertEqual(amounts, {"EVE-1": 300.0, "EVE-2": 50.0})
        eve_1_ids = incidents.set_index("incdnt_sid").loc["EVE-1", "recovery_sid"]
        self.assertEqual(eve_1_ids, "EVE-1-R1 | EVE-1-R2")

    def test_type_breakdown_uses_raw_amounts_and_unique_incidents(self):
        _, metrics = prepare_vozmeshenie_views(_sample_df())
        breakdown = {item["type"]: item for item in metrics["type_breakdown"]}

        self.assertEqual(breakdown["Компенсация от клиента"]["unique_incidents"], 2)
        self.assertEqual(breakdown["Компенсация от клиента"]["amount"], 150.0)
        self.assertEqual(breakdown["Страховая выплата"]["unique_incidents"], 1)
        self.assertEqual(breakdown["Страховая выплата"]["amount"], 200.0)

    def test_header_is_only_place_that_mentions_rows(self):
        incidents, metrics = prepare_vozmeshenie_views(_sample_df())
        report = build_deterministic_full_report(
            incidents,
            "vozmeshenie_ior",
            is_summarization_only=True,
            voz_metrics=metrics,
        )

        self.assertEqual(report.lower().count("строк"), 1)
        self.assertIn("Количество строк выгрузки", report)
        self.assertIn("Количество уникальных инцидентов с возмещениями", report)
        self.assertIn("350.00 ₽", report)
        for forbidden in ("общая сумма потерь", "прямые потери", "чистые потери", "net loss"):
            self.assertNotIn(forbidden, report.lower())

    def test_specialized_profile_has_no_loss_metrics(self):
        incidents, _ = prepare_vozmeshenie_views(_sample_df())
        profile = profile_vozmeshenie_dataframe(incidents)

        self.assertIn("Количество уникальных инцидентов", profile)
        self.assertIn("Сумма полученных возмещений", profile)
        self.assertNotIn("строк", profile.lower())
        self.assertNotIn("потер", profile.lower())
        self.assertNotIn("net loss", profile.lower())

    def test_header_formats_full_extract_metrics(self):
        _, metrics = prepare_vozmeshenie_views(_sample_df())
        header = format_vozmeshenie_header(metrics)
        self.assertIn("3", header)
        self.assertIn("2", header)
        self.assertIn("350.00 ₽", header)

    def test_llm_text_is_scrubbed_from_rows_and_loss_claims(self):
        text = "\n".join([
            "### 1. Общая сводка",
            "В анализе 3 строки выгрузки.",
            "Общая сумма потерь: 999 999 ₽.",
            "Ущерб по инцидентам составил 10 ₽.",
            "Количество уникальных инцидентов: 2.",
        ])
        cleaned = sanitize_vozmeshenie_narrative(text)
        self.assertNotIn("строк", cleaned.lower())
        self.assertNotIn("потер", cleaned.lower())
        self.assertNotIn("ущерб", cleaned.lower())
        self.assertIn("Количество уникальных инцидентов: 2", cleaned)


if __name__ == "__main__":
    unittest.main()
