"""Аналитика основного реестра ПАО Сбербанк (1 строка ≈ 1 ИОР)."""
from __future__ import annotations

import pandas as pd

from .ior_hypothesis_analysis import prepare_generic

PRESET = "ior_period_pao_sberbank"


def prepare(df: pd.DataFrame):
    bundle = prepare_generic(df, PRESET)
    bundle.prompt_rules += (
        " Для основного реестра отдельно учитывай тип события, источник, ЦПР, процесс и оргструктуру. "
        "Слабую заполненность main financial fields не превращай в вывод о нулевых потерях."
    )
    bundle.hypothesis_topics = (
        "процессной и типологической структуры ИОР",
        "организационной концентрации",
        "текстовых корневых факторов конкретных EVE",
    )
    return bundle
