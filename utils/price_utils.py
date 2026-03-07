"""Price and pip utility functions."""

from __future__ import annotations

import math


def pip_size(symbol: str) -> float:
    """
    Returns the size of one pip for the given symbol.

    Convention:
      JPY pairs  → 0.01  (2nd decimal)
      All others → 0.0001 (4th decimal)
    """
    if "JPY" in symbol.upper():
        return 0.01
    return 0.0001


def price_to_pips(price_diff: float, symbol: str) -> float:
    """Convert an absolute price difference to pips."""
    return abs(price_diff) / pip_size(symbol)


def pips_to_price(pips: float, symbol: str) -> float:
    """Convert pips to a price difference."""
    return pips * pip_size(symbol)


def round_price(price: float, digits: int) -> float:
    """Round a price to the broker's required decimal places."""
    factor = 10 ** digits
    return math.floor(price * factor + 0.5) / factor


def normalise_lots(
    lots: float,
    lot_step: float,
    lot_min: float,
    lot_max: float,
) -> float:
    """
    Snap *lots* to the nearest valid lot step and clamp to [lot_min, lot_max].
    """
    stepped = math.floor(lots / lot_step) * lot_step
    clamped = max(lot_min, min(stepped, lot_max))
    return round(clamped, 2)


def pip_distance(a: float, b: float, symbol: str) -> float:
    """Absolute pip distance between two prices."""
    return price_to_pips(abs(a - b), symbol)
