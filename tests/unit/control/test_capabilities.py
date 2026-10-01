"""The capability declaration (spec section 4.1, issue #158)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from custom_components.nwp500.control.capabilities import build_capabilities

from .conftest import capabilities


class TestDeclaration:
    def test_what_runs_and_the_entry_budget_only(self):
        """The adapter applies the plan; it declares nothing else (#192)."""
        attrs = build_capabilities({}, feature_version="v").as_attributes()

        assert set(attrs) == {
            "protocols",
            "protocol_versions",
            "feature_version",
            "mode",
            "setpoint_resolution_c",
            "horizon_h",
            "near_term_lead_min",
            "entry_limit",
            "entry_reserve",
            "entries_available",
            "version",
        }
        assert attrs["protocols"] == ["1", "0"]
        # The newest minor of each major: 1.1 adds `reassert`.
        assert attrs["protocol_versions"] == ["1.2", "0"]
        assert attrs["mode"] == "shadow"
        assert attrs["horizon_h"] == 144
        assert attrs["near_term_lead_min"] == 2
        assert attrs["entry_limit"] == 16
        assert attrs["entry_reserve"] == 2
        assert attrs["setpoint_resolution_c"] == 0.5
        assert attrs["entries_available"] is None


class TestVersion:
    def test_is_short_and_stable(self):
        assert len(capabilities().version) == 8
        assert capabilities().version == capabilities().version

    def test_changes_with_an_option(self):
        before = capabilities().version
        after = capabilities(control_reservation_entry_limit=9).version
        assert before != after

    def test_ignores_the_live_entry_count(self):
        declaration = capabilities()
        assert (
            replace(declaration, entries_available=3).version
            == declaration.version
        )


@pytest.mark.parametrize(
    ("options", "mode"),
    [
        ({"control_mode": "live"}, "live"),
        ({"control_mode": "live", "control_live_segments": True}, "live"),
        # An earlier version's live with its segments switch off wrote
        # nothing, and still writes nothing until the form is saved.
        ({"control_mode": "live", "control_live_segments": False}, "shadow"),
        ({"control_mode": "shadow"}, "shadow"),
        ({"control_mode": "disabled"}, "disabled"),
    ],
)
def test_live_follows_the_plan(options, mode):
    from custom_components.nwp500.const import control_follows_plan

    assert control_follows_plan(options) is (mode == "live")
