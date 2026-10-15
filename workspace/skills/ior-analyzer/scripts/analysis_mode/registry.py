"""Money strategies are independent of the existing preset registry."""
from .direct_loss import DirectLossStrategy

MONEY_FILTERS = {"прямые потери": DirectLossStrategy()}
