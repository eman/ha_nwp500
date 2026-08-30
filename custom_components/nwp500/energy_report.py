"""Shaping of the device's energy usage history into a report.

The device keeps its own daily energy totals and reports them on request,
split into what the heat pump drew and what the resistive elements drew.
That split is the interesting part: element usage is the expensive kind,
and the device is the only thing that measures it.

The raw response is awkward to consume directly -- a day carries no date,
only its position in the month's list -- so this module turns it into a
report a template or script can read without knowing the protocol.
"""

from __future__ import annotations

import calendar
from typing import Any

# The device reports whole Watt-hours; kWh is what a utility bill and the
# Home Assistant energy UI speak, so the report carries both.
_WH_PER_KWH = 1000.0


def _rounded_kwh(watt_hours: int) -> float:
    """Convert to kWh at a resolution worth reporting."""
    return round(watt_hours / _WH_PER_KWH, 3)


def _usage(source: dict[str, Any]) -> dict[str, Any]:
    """Shape one usage record -- a single day, or a whole-period total."""
    heat_pump_wh = int(source.get("heat_pump_usage", 0) or 0)
    heat_element_wh = int(source.get("heat_element_usage", 0) or 0)
    total_wh = heat_pump_wh + heat_element_wh

    return {
        "heat_pump_wh": heat_pump_wh,
        "heat_element_wh": heat_element_wh,
        "total_wh": total_wh,
        "heat_pump_kwh": _rounded_kwh(heat_pump_wh),
        "heat_element_kwh": _rounded_kwh(heat_element_wh),
        "total_kwh": _rounded_kwh(total_wh),
        # Share of the period's energy that came from the heat pump. The
        # remainder is resistive element usage, which costs roughly three
        # times as much per unit of heat.
        "heat_pump_percent": (
            round(heat_pump_wh / total_wh * 100.0, 1) if total_wh else 0.0
        ),
        "heat_pump_hours": int(source.get("heat_pump_time", 0) or 0),
        "heat_element_hours": int(source.get("heat_element_time", 0) or 0),
    }


def _days(month: dict[str, Any]) -> list[dict[str, Any]]:
    """Date-stamp each day in a month's list.

    The protocol carries no date per day -- position in the list is the day
    of the month, counting from 1. A month can therefore only be read
    correctly together with the year and month it was requested for, which
    is why dating happens here rather than being left to the caller.
    """
    year = int(month.get("year", 0) or 0)
    month_number = int(month.get("month", 0) or 0)
    days_in_month = (
        calendar.monthrange(year, month_number)[1]
        if year and 1 <= month_number <= 12
        else 31
    )

    dated: list[dict[str, Any]] = []
    for day_number, day in enumerate(month.get("data") or [], start=1):
        if not isinstance(day, dict) or day_number > days_in_month:
            # A device that reports more entries than the month has days is
            # padding, not reporting a 32nd of January.
            continue
        entry: dict[str, Any] = {"day": day_number}
        if year and month_number:
            entry["date"] = f"{year:04d}-{month_number:02d}-{day_number:02d}"
        entry.update(_usage(day))
        dated.append(entry)
    return dated


def _period_total(days: list[dict[str, Any]]) -> dict[str, Any]:
    """Total one month from its days.

    The response's own `total` covers everything requested, so a per-month
    total has to be summed here for a multi-month report to be readable.
    """
    return _usage(
        {
            "heat_pump_usage": sum(day["heat_pump_wh"] for day in days),
            "heat_element_usage": sum(day["heat_element_wh"] for day in days),
            "heat_pump_time": sum(day["heat_pump_hours"] for day in days),
            "heat_element_time": sum(day["heat_element_hours"] for day in days),
        }
    )


def build_report(
    response: dict[str, Any], *, mac_address: str
) -> dict[str, Any]:
    """Turn an `EnergyUsageResponse` dump into an on-demand report.

    Args:
        response: `EnergyUsageResponse.model_dump()` output.
        mac_address: The device the report is for.

    Returns:
        A JSON-serialisable report: the whole-request total, then one entry
        per month carrying its own total and its date-stamped days.
    """
    months: list[dict[str, Any]] = []
    for month in response.get("usage") or []:
        if not isinstance(month, dict):
            continue
        days = _days(month)
        months.append(
            {
                "year": int(month.get("year", 0) or 0),
                "month": int(month.get("month", 0) or 0),
                "total": _period_total(days),
                "days": days,
            }
        )

    return {
        "mac_address": mac_address,
        "total": _usage(response.get("total") or {}),
        "months": months,
    }
