"""Единая точка dispatch предметных анализаторов."""
from __future__ import annotations

from . import deleted_ior_analysis
from . import financial_consequences_analysis
from . import ior_hypothesis_analysis
from . import ior_period_pao_sberbank_analysis
from . import nonfinancial_consequences_analysis
from . import report_period_specific_ior_analysis
from . import vozmeshenie_analysis


ANALYZERS = {
    "vozmeshenie_ior": vozmeshenie_analysis,
    "financial_consequences_ior": financial_consequences_analysis,
    "ior_nonfinancial_consequences": nonfinancial_consequences_analysis,
    "deleted_ior": deleted_ior_analysis,
    "ior_period_pao_sberbank": ior_period_pao_sberbank_analysis,
    "report_period_specific_ior": report_period_specific_ior_analysis,
    "ior_hypothesis": ior_hypothesis_analysis,
}


def get_analyzer(preset: str):
    normalized = (preset or "ior_hypothesis").removesuffix("_v2")
    return ANALYZERS.get(normalized)


def has_analyzer(preset: str) -> bool:
    return get_analyzer(preset) is not None
