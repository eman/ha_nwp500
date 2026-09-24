"""Parsing the plan document (spec section 3, issue #158)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from custom_components.nwp500.control.intent import (
    REASON_DUPLICATE_ID,
    REASON_INVALID_DOCUMENT,
    REASON_UNORDERED_SEGMENTS,
    REASON_UNSUPPORTED_PROTOCOL,
    SETPOINT_MIN,
    IntentRejected,
    document_from_attributes,
    parse_plan,
    truncate_to_minute,
)

from .conftest import grant, make_document, segment


class TestAcceptedPlans:
    def test_spec_example_is_accepted(self, now, parse):
        """The example in section 3.6, relative to now."""
        plan = parse(
            make_document(
                now,
                [
                    segment(
                        now,
                        "s1",
                        0,
                        mode="heat_pump",
                        setpoint="min",
                        purpose="hold_off",
                    ),
                    segment(now, "s2", 330, setpoint_f=140, purpose="charge"),
                    segment(
                        now, "s3", 570, mode="energy_saver", setpoint_f=135
                    ),
                    segment(now, "s4", 1020, setpoint="min"),
                ],
                grants=[grant(now, "g1", 360, 540, max_f=146)],
                plan_id="opaque",
            )
        )

        assert plan.intent_id == "i-1"
        assert plan.extra == {"plan_id": "opaque"}
        assert [s.id for s in plan.segments] == ["s1", "s2", "s3", "s4"]
        s1, s2, s3, s4 = plan.segments
        assert s1.setpoint_raw is None
        assert s1.setpoint_form == SETPOINT_MIN
        assert s1.extra == {"purpose": "hold_off"}
        # 140 degF is 60 degC, 120 half-degrees; 135 degF rounds to 114.
        assert s2.setpoint_raw == 120
        assert s3.setpoint_raw == 114
        # A segment without a mode keeps the previous one.
        assert s2.mode == "heat_pump"
        assert s2.mode_given is False
        assert s4.mode == "energy_saver"
        (g1,) = plan.grants
        assert g1.max_raw == 127

    def test_times_are_truncated_to_the_minute(self, now, parse):
        start = now + timedelta(minutes=5, seconds=42)
        plan = parse(
            make_document(
                now,
                [
                    {
                        "id": "s",
                        "start": start.isoformat(),
                        "mode": "heat_pump",
                        "setpoint_f": 130,
                    }
                ],
            )
        )
        assert plan.segments[0].start == truncate_to_minute(start)

    def test_celsius(self, now, parse):
        plan = parse(
            make_document(
                now, [segment(now, "s", 0, mode="heat_pump", setpoint_c=55.2)]
            )
        )
        assert plan.segments[0].setpoint_raw == 110

    def test_empty_segments_stop_the_plan(self, now, parse):
        assert parse(make_document(now, [])).segments == ()

    def test_grants_are_optional(self, now, parse):
        plan = parse(
            make_document(
                now, [segment(now, "s", 0, mode="heat_pump", setpoint="min")]
            )
        )
        assert plan.grants == ()

    def test_z_offset(self, now, parse):
        document = make_document(now)
        document["issued_at"] = now.replace(tzinfo=None).isoformat() + "Z"
        assert parse(document).issued_at == now

    def test_segment_at(self, now, parse):
        plan = parse(
            make_document(
                now,
                [
                    segment(now, "a", 10, mode="heat_pump", setpoint_f=130),
                    segment(now, "b", 20, setpoint_f=140),
                ],
            )
        )
        assert plan.segment_at(now) is None
        assert plan.segment_at(now + timedelta(minutes=10)).id == "a"
        assert plan.segment_at(now + timedelta(minutes=25)).id == "b"
        assert plan.next_segment_after(now + timedelta(minutes=10)).id == "b"
        assert plan.next_segment_after(now + timedelta(minutes=20)) is None

    def test_as_document_round_trips(self, now, parse):
        document = make_document(
            now,
            [
                segment(
                    now, "a", 0, mode="heat_pump", setpoint="min", note="x"
                ),
                segment(now, "b", 60, setpoint_c=52.5),
                segment(now, "c", 120, mode="electric", setpoint_f=140),
            ],
            grants=[grant(now, "g", 10, 50, max_c=63.5, why="sun")],
            plan_id="p",
        )
        plan = parse(document)

        again = parse(plan.as_document())

        assert again == plan
        stored = plan.as_document()
        assert stored["segments"][0]["setpoint"] == "min"
        assert stored["segments"][1]["setpoint_c"] == 52.5
        assert "mode" not in stored["segments"][1]
        assert stored["grants"][0]["max_c"] == 63.5
        assert stored["grants"][0]["why"] == "sun"


class TestRejectedDocuments:
    @staticmethod
    def _rejects(parse, document, reason):
        with pytest.raises(IntentRejected) as exc_info:
            parse(document)
        assert exc_info.value.reason == reason
        return exc_info.value

    @pytest.mark.parametrize(
        "missing", ["protocol", "intent_id", "issued_at", "segments"]
    )
    def test_missing_required_key(self, now, parse, missing):
        document = make_document(now)
        del document[missing]
        self._rejects(parse, document, REASON_INVALID_DOCUMENT)

    def test_unsupported_protocol(self, now, parse):
        self._rejects(
            parse, make_document(now, protocol="1"), REASON_UNSUPPORTED_PROTOCOL
        )

    def test_minor_versions_are_accepted(self, now, parse):
        assert parse(make_document(now, protocol="0.2")).intent_id == "i-1"

    def test_intent_id(self, now, parse):
        self._rejects(
            parse,
            make_document(now, intent_id="x" * 65),
            REASON_INVALID_DOCUMENT,
        )
        self._rejects(
            parse, make_document(now, intent_id=""), REASON_INVALID_DOCUMENT
        )

    def test_naive_timestamp(self, now, parse):
        document = make_document(now)
        document["issued_at"] = now.replace(tzinfo=None).isoformat()
        assert (
            "offset"
            in self._rejects(parse, document, REASON_INVALID_DOCUMENT).detail
        )

    def test_first_segment_needs_a_mode(self, now, parse):
        self._rejects(
            parse,
            make_document(now, [segment(now, "s", 0, setpoint_f=130)]),
            REASON_INVALID_DOCUMENT,
        )

    @pytest.mark.parametrize(
        "setpoint",
        [
            {},
            {"setpoint_f": 130, "setpoint_c": 55},
            {"setpoint": "min", "setpoint_f": 130},
            {"setpoint": "max"},
            {"setpoint": 130},
            {"setpoint_f": "130"},
            {"setpoint_c": True},
        ],
    )
    def test_setpoint_forms(self, now, parse, setpoint):
        self._rejects(
            parse,
            make_document(
                now, [segment(now, "s", 0, mode="heat_pump", **setpoint)]
            ),
            REASON_INVALID_DOCUMENT,
        )

    def test_segments_must_be_in_order(self, now, parse):
        self._rejects(
            parse,
            make_document(
                now,
                [
                    segment(now, "a", 60, mode="heat_pump", setpoint_f=130),
                    segment(now, "b", 30, setpoint_f=140),
                ],
            ),
            REASON_UNORDERED_SEGMENTS,
        )

    def test_same_minute_after_truncation_is_unordered(self, now, parse):
        a = segment(now, "a", 0, mode="heat_pump", setpoint_f=130)
        b = segment(now, "b", 0.5, setpoint_f=140)
        self._rejects(
            parse, make_document(now, [a, b]), REASON_UNORDERED_SEGMENTS
        )

    def test_duplicate_ids_across_segments_and_grants(self, now, parse):
        self._rejects(
            parse,
            make_document(
                now,
                [segment(now, "x", 0, mode="heat_pump", setpoint_f=130)],
                grants=[grant(now, "x", 10, 20, max_f=146)],
            ),
            REASON_DUPLICATE_ID,
        )

    def test_mode_must_be_a_string(self, now, parse):
        self._rejects(
            parse,
            make_document(now, [segment(now, "s", 0, mode=3, setpoint_f=130)]),
            REASON_INVALID_DOCUMENT,
        )

    @pytest.mark.parametrize(
        "bad",
        [
            {"id": "g", "start": "x", "end": "y", "max_f": 146},
            {"id": "g", "end": "2026-10-04T10:00:00+00:00", "max_f": 146},
            {
                "id": "g",
                "start": "2026-10-04T09:00:00+00:00",
                "end": "2026-10-04T10:00:00+00:00",
            },
            {
                "id": "g",
                "start": "2026-10-04T09:00:00+00:00",
                "end": "2026-10-04T10:00:00+00:00",
                "max_f": "146",
            },
        ],
    )
    def test_grant_structure(self, now, parse, bad):
        self._rejects(
            parse, make_document(now, grants=[bad]), REASON_INVALID_DOCUMENT
        )

    def test_grants_must_be_a_list(self, now, parse):
        self._rejects(
            parse, make_document(now, grants={}), REASON_INVALID_DOCUMENT
        )

    def test_segments_must_be_a_list(self, now, parse):
        self._rejects(
            parse, make_document(now, segments={}), REASON_INVALID_DOCUMENT
        )

    def test_items_must_be_objects(self, now, parse):
        self._rejects(parse, make_document(now, ["s"]), REASON_INVALID_DOCUMENT)
        self._rejects(
            parse, make_document(now, grants=["g"]), REASON_INVALID_DOCUMENT
        )


def test_a_backwards_grant_is_kept_for_evaluate(now, parse):
    """A grant whose end is before its start is rejected alone, later."""
    plan = parse(
        make_document(now, grants=[grant(now, "g", 20, 10, max_f=146)])
    )
    assert plan.grants[0].end < plan.grants[0].start


def test_document_from_attributes_drops_presentation_keys():
    assert document_from_attributes(
        {"friendly_name": "x", "icon": "mdi:x", "protocol": "0", "plan_id": "p"}
    ) == {"protocol": "0", "plan_id": "p"}


def test_parse_plan_directly(now):
    plan = parse_plan(make_document(now))
    assert plan.segments == ()
