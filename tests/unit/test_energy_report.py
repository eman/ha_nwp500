"""Tests for the on-demand energy usage report.

The device reports daily totals with no date attached -- position in the
month's list is the day -- so the shaping these tests cover is what makes
the response readable without knowing the protocol.
"""

from __future__ import annotations

from custom_components.nwp500 import energy_report

# One day of heat pump use, one day where the elements did the work.
RESPONSE = {
    "total": {
        "heat_pump_usage": 3000,
        "heat_element_usage": 1000,
        "heat_pump_time": 30,
        "heat_element_time": 2,
    },
    "usage": [
        {
            "year": 2026,
            "month": 2,
            "data": [
                {
                    "heat_pump_usage": 2000,
                    "heat_element_usage": 0,
                    "heat_pump_time": 20,
                    "heat_element_time": 0,
                },
                {
                    "heat_pump_usage": 1000,
                    "heat_element_usage": 1000,
                    "heat_pump_time": 10,
                    "heat_element_time": 2,
                },
            ],
        }
    ],
}


class TestBuildReport:
    """The report's shape."""

    def test_days_are_dated_by_their_position(self):
        """The protocol carries no date; the list index is the day."""
        report = energy_report.build_report(RESPONSE, mac_address="AA:BB")

        days = report["months"][0]["days"]
        assert [day["day"] for day in days] == [1, 2]
        assert [day["date"] for day in days] == ["2026-02-01", "2026-02-02"]

    def test_usage_is_reported_in_both_wh_and_kwh(self):
        report = energy_report.build_report(RESPONSE, mac_address="AA:BB")

        day = report["months"][0]["days"][1]
        assert day["heat_pump_wh"] == 1000
        assert day["heat_element_wh"] == 1000
        assert day["total_wh"] == 2000
        assert day["total_kwh"] == 2.0

    def test_the_heat_pump_share_is_computed_per_period(self):
        """The split is the point: element usage is the expensive kind."""
        report = energy_report.build_report(RESPONSE, mac_address="AA:BB")

        assert report["months"][0]["days"][0]["heat_pump_percent"] == 100.0
        assert report["months"][0]["days"][1]["heat_pump_percent"] == 50.0
        assert report["total"]["heat_pump_percent"] == 75.0

    def test_a_month_is_totalled_from_its_days(self):
        """The response's own total covers everything requested at once."""
        report = energy_report.build_report(RESPONSE, mac_address="AA:BB")

        month_total = report["months"][0]["total"]
        assert month_total["heat_pump_wh"] == 3000
        assert month_total["heat_element_wh"] == 1000
        assert month_total["heat_pump_hours"] == 30

    def test_an_idle_period_does_not_divide_by_zero(self):
        report = energy_report.build_report(
            {"total": {}, "usage": []}, mac_address="AA:BB"
        )

        assert report["total"]["total_wh"] == 0
        assert report["total"]["heat_pump_percent"] == 0.0
        assert report["months"] == []

    def test_padding_past_the_end_of_the_month_is_dropped(self):
        """February has 28 days in 2026; a 29th entry is padding."""
        response = {
            "total": {},
            "usage": [
                {
                    "year": 2026,
                    "month": 2,
                    "data": [{"heat_pump_usage": 1}] * 31,
                }
            ],
        }

        report = energy_report.build_report(response, mac_address="AA:BB")

        assert len(report["months"][0]["days"]) == 28
        assert report["months"][0]["days"][-1]["date"] == "2026-02-28"

    def test_a_leap_day_is_kept(self):
        response = {
            "total": {},
            "usage": [
                {
                    "year": 2028,
                    "month": 2,
                    "data": [{"heat_pump_usage": 1}] * 29,
                }
            ],
        }

        report = energy_report.build_report(response, mac_address="AA:BB")

        assert report["months"][0]["days"][-1]["date"] == "2028-02-29"

    def test_the_report_names_the_device_it_is_for(self):
        report = energy_report.build_report(RESPONSE, mac_address="AA:BB:CC")

        assert report["mac_address"] == "AA:BB:CC"

    def test_the_report_is_json_serialisable(self):
        """It is returned to a service caller, so it must survive the trip."""
        import json

        json.dumps(energy_report.build_report(RESPONSE, mac_address="AA:BB"))


# The monthly query: one entry per year, no month, a month per item.
MONTHLY_RESPONSE = {
    "total": {
        "heat_pump_usage": 900000,
        "heat_element_usage": 100000,
        "heat_pump_time": 9000,
        "heat_element_time": 200,
    },
    "usage": [
        {
            "year": 2026,
            "month": None,
            "data": [
                {
                    "heat_pump_usage": 50000,
                    "heat_element_usage": 10000,
                    "heat_pump_time": 500,
                    "heat_element_time": 20,
                },
                {
                    "heat_pump_usage": 40000,
                    "heat_element_usage": 0,
                    "heat_pump_time": 400,
                    "heat_element_time": 0,
                },
            ],
        }
    ],
}


class TestBuildMonthlyReport:
    """The per-year report's shape."""

    def test_months_are_dated_by_their_position(self):
        report = energy_report.build_monthly_report(
            MONTHLY_RESPONSE, mac_address="AA:BB"
        )

        months = report["years"][0]["months"]
        assert [m["month"] for m in months] == [1, 2]
        assert [m["date"] for m in months] == ["2026-01", "2026-02"]

    def test_a_year_is_totalled_from_its_months(self):
        """The response's own total is lifetime, so the year's must be summed."""
        report = energy_report.build_monthly_report(
            MONTHLY_RESPONSE, mac_address="AA:BB"
        )

        year = report["years"][0]
        assert year["year"] == 2026
        assert year["total"]["heat_pump_wh"] == 90000
        assert year["total"]["heat_element_wh"] == 10000
        assert year["total"]["total_kwh"] == 100.0
        assert year["total"]["heat_pump_hours"] == 900

    def test_the_lifetime_total_is_named_as_such(self):
        report = energy_report.build_monthly_report(
            MONTHLY_RESPONSE, mac_address="AA:BB"
        )

        assert report["lifetime_total"]["total_kwh"] == 1000.0
        assert "total" not in report

    def test_a_thirteenth_month_is_dropped(self):
        response = {
            "total": {},
            "usage": [
                {
                    "year": 2026,
                    "month": None,
                    "data": [{"heat_pump_usage": 1}] * 13,
                }
            ],
        }

        report = energy_report.build_monthly_report(
            response, mac_address="AA:BB"
        )

        assert len(report["years"][0]["months"]) == 12

    def test_an_unknown_year_carries_no_dates(self):
        response = {
            "total": {},
            "usage": [{"month": None, "data": [{"heat_pump_usage": 1}]}],
        }

        report = energy_report.build_monthly_report(
            response, mac_address="AA:BB"
        )

        assert "date" not in report["years"][0]["months"][0]

    def test_the_report_is_json_serialisable(self):
        import json

        json.dumps(
            energy_report.build_monthly_report(
                MONTHLY_RESPONSE, mac_address="AA:BB"
            )
        )
