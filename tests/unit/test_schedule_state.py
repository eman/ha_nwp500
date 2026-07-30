"""Tests for the canonical schedule representation (issue #103)."""

from __future__ import annotations

from custom_components.nwp500 import schedule_state


def _entry(**overrides):
    entry = {
        "enable": 2,
        "week": 42,
        "hour": 6,
        "min": 30,
        "mode": 3,
        "param": 120,
    }
    entry.update(overrides)
    return entry


def _tou_period(**overrides):
    period = {
        "season": 4095,
        "week": 254,
        "start_hour": 0,
        "start_min": 0,
        "end_hour": 6,
        "end_min": 0,
        "price_min": 10,
        "price_max": 20,
        "decimal_point": 2,
    }
    period.update(overrides)
    return period


class TestEnabledFlag:
    """`reservation_use` follows the device bool convention: 2=on, 1=off."""

    def test_two_means_enabled(self):
        assert schedule_state.is_enabled({"reservation_use": 2}) is True

    def test_one_means_disabled(self):
        assert schedule_state.is_enabled({"reservation_use": 1}) is False

    def test_missing_means_disabled(self):
        assert schedule_state.is_enabled({}) is False


class TestCanonicalForm:
    """The canonical form must not depend on the order entries arrive in."""

    def test_order_independent(self):
        a = {
            "reservation_use": 2,
            "reservation": [_entry(hour=6), _entry(hour=18)],
        }
        b = {
            "reservation_use": 2,
            "reservation": [_entry(hour=18), _entry(hour=6)],
        }

        assert schedule_state.reservation_canonical(
            a
        ) == schedule_state.reservation_canonical(b)

    def test_distinguishes_different_entries(self):
        a = {"reservation_use": 2, "reservation": [_entry(mode=3)]}
        b = {"reservation_use": 2, "reservation": [_entry(mode=4)]}

        assert schedule_state.reservation_canonical(
            a
        ) != schedule_state.reservation_canonical(b)

    def test_distinguishes_enabled_state(self):
        a = {"reservation_use": 2, "reservation": [_entry()]}
        b = {"reservation_use": 1, "reservation": [_entry()]}

        assert schedule_state.reservation_canonical(
            a
        ) != schedule_state.reservation_canonical(b)

    def test_ignores_computed_fields(self):
        """model_dump() includes computed fields; only raw ones count."""
        plain = {"reservation_use": 2, "reservation": [_entry()]}
        with_computed = {
            "reservation_use": 2,
            "reservation": [
                _entry() | {"enabled": True, "days": ["Monday"]},
            ],
        }

        assert schedule_state.reservation_canonical(
            plain
        ) == schedule_state.reservation_canonical(with_computed)

    def test_empty_schedule(self):
        assert schedule_state.reservation_canonical({}) == (False, ())

    def test_tou_uses_its_own_field_set(self):
        """TOU entries carry season/price fields reservations do not."""
        a = {"reservation_use": 2, "reservation": [_tou_period(price_max=20)]}
        b = {"reservation_use": 2, "reservation": [_tou_period(price_max=99)]}

        assert schedule_state.tou_canonical(a) != schedule_state.tou_canonical(
            b
        )


class TestScheduleHash:
    """The hash is what a consumer compares to check it is in sync."""

    def test_same_program_same_hash_regardless_of_order(self):
        a = {
            "reservation_use": 2,
            "reservation": [_entry(hour=6), _entry(hour=18)],
        }
        b = {
            "reservation_use": 2,
            "reservation": [_entry(hour=18), _entry(hour=6)],
        }

        assert schedule_state.schedule_hash(
            schedule_state.reservation_canonical(a)
        ) == schedule_state.schedule_hash(
            schedule_state.reservation_canonical(b)
        )

    def test_different_program_different_hash(self):
        a = {"reservation_use": 2, "reservation": [_entry(hour=6)]}
        b = {"reservation_use": 2, "reservation": [_entry(hour=7)]}

        assert schedule_state.schedule_hash(
            schedule_state.reservation_canonical(a)
        ) != schedule_state.schedule_hash(
            schedule_state.reservation_canonical(b)
        )

    def test_is_stable_across_calls(self):
        schedule = {"reservation_use": 2, "reservation": [_entry()]}
        canonical = schedule_state.reservation_canonical(schedule)

        assert schedule_state.schedule_hash(
            canonical
        ) == schedule_state.schedule_hash(canonical)

    def test_is_a_short_hex_string(self):
        digest = schedule_state.schedule_hash(
            schedule_state.reservation_canonical({})
        )

        assert len(digest) == 16
        assert all(c in "0123456789abcdef" for c in digest)


class TestEntries:
    """Entries are copied out so callers cannot mutate coordinator state."""

    def test_returns_the_entries(self):
        schedule = {"reservation": [_entry(hour=6), _entry(hour=18)]}

        assert len(schedule_state.reservation_entries(schedule)) == 2

    def test_copies_each_entry(self):
        original = _entry()
        schedule = {"reservation": [original]}

        returned = schedule_state.reservation_entries(schedule)
        returned[0]["hour"] = 23

        assert original["hour"] == 6

    def test_missing_key_yields_empty_list(self):
        assert schedule_state.reservation_entries({}) == []
