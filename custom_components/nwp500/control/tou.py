"""Whether a minute falls inside a TOU window (spec section 5.8).

An entry's mode does not take effect while a TOU window is in force. The
window, on the unit measured, is the TOU program's highest-priced period of
the day. This module answers the question for planning only: whether TOU is
overridden at that future minute cannot be known, so the answer assumes it
is not.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime

from .entries import week_bit


def _applies(period: Mapping[str, int], local: datetime) -> bool:
    season_ok = bool(period.get("season", 0) & (1 << (local.month - 1)))
    return season_ok and bool(period.get("week", 0) & week_bit(local))


def in_tou_window(
    periods: Iterable[Mapping[str, int]], local: datetime
) -> bool:
    """Whether `local` falls in the day's highest-priced TOU period."""
    today = [p for p in periods if _applies(p, local)]
    if not today:
        return False
    top = max(p.get("price_max", 0) for p in today)
    minute = local.hour * 60 + local.minute
    for period in today:
        if period.get("price_max", 0) != top:
            continue
        start = period["start_hour"] * 60 + period["start_min"]
        end = period["end_hour"] * 60 + period["end_min"]
        if start <= minute <= end:
            return True
    return False
