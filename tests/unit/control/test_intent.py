"""Document-level validation (spec section 3, issue #158)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from custom_components.nwp500.control.intent import (
    REASON_CHARGE_OVERLAPS_HOLD_OFF,
    REASON_DUPLICATE_DIRECTIVE_ID,
    REASON_GRANT_OVERLAPS_MODE,
    REASON_INVALID_DOCUMENT,
    REASON_INVALID_VALIDITY,
    REASON_INVALID_WINDOW,
    REASON_STALE_ON_RECEIPT,
    REASON_UNSUPPORTED_PROTOCOL,
    IntentRejected,
    document_from_attributes,
)

from .conftest import directive, make_document, ts


class TestAcceptedDocuments:
    def test_spec_example_is_accepted(self, now, parse):
        """The example in section 3.5, relative to now."""
        document = make_document(
            now,
            [
                directive(now, "hold_off", "d1", start=0, end=330),
                directive(
                    now,
                    "charge",
                    "d2",
                    start=330,
                    end=570,
                    target_f=140,
                    purpose="demand",
                ),
                directive(
                    now, "surplus_grant", "d3", start=360, end=540, max_f=146
                ),
            ],
            valid_for=75,
            plan_id="opaque-to-the-feature",
        )

        intent = parse(document)

        assert intent.intent_id == "i-1"
        assert intent.extra == {"plan_id": "opaque-to-the-feature"}
        assert [d.id for d in intent.directives] == ["d1", "d2", "d3"]
        charge = intent.directives[1]
        # 140 degF is 60 degC, 120 half-degrees.
        assert charge.temperature_raw == 120
        assert charge.extra["purpose"] == "demand"
        assert intent.directives[2].temperature_raw == 127

    def test_directives_are_ordered_by_start(self, now, parse):
        document = make_document(
            now,
            [
                directive(
                    now, "charge", "late", start=200, end=400, target_f=130
                ),
                directive(
                    now, "charge", "early", start=0, end=150, target_f=130
                ),
            ],
        )

        intent = parse(document)

        assert [d.id for d in intent.directives] == ["early", "late"]

    def test_empty_directives_is_an_explicit_no_intent(self, now, parse):
        assert parse(make_document(now, [])).directives == ()

    def test_celsius_is_accepted_and_quantised(self, now, parse):
        document = make_document(
            now, [directive(now, "charge", target_c=55.2, end=180)]
        )
        intent = parse(document)
        # 55.2 degC rounds to 110 half-degrees (55.0).
        assert intent.directives[0].temperature_raw == 110

    def test_mode_directive_defaults_request_to_false(self, now, parse):
        intent = parse(
            make_document(now, [directive(now, "mode", mode="energy_saver")])
        )
        assert intent.directives[0].mode == "energy_saver"
        assert intent.directives[0].request is False

    def test_mode_request_is_read(self, now, parse):
        intent = parse(
            make_document(
                now, [directive(now, "mode", mode="heat_pump", request=True)]
            )
        )
        assert intent.directives[0].request is True

    def test_z_offset_is_an_offset(self, now, parse):
        document = make_document(now)
        document["issued_at"] = (
            now.replace(tzinfo=None).isoformat(timespec="seconds") + "Z"
        )
        assert parse(document).issued_at == now

    @pytest.mark.parametrize(
        ("first", "second"),
        [
            ("mode", "charge"),
            ("mode", "hold_off"),
            ("surplus_grant", "charge"),
            ("surplus_grant", "hold_off"),
        ],
    )
    def test_permitted_overlaps(self, now, parse, first, second):
        """Only charge/hold_off and surplus_grant/mode may not overlap."""
        extras = {
            "mode": {"mode": "energy_saver"},
            "charge": {"target_f": 130},
            "surplus_grant": {"max_f": 145},
            "hold_off": {},
        }
        document = make_document(
            now,
            [
                directive(now, first, "a", start=0, end=200, **extras[first]),
                directive(
                    now, second, "b", start=100, end=300, **extras[second]
                ),
            ],
        )
        assert len(parse(document).directives) == 2

    def test_as_document_round_trips(self, now, parse):
        document = make_document(
            now,
            [
                directive(now, "charge", "c", target_c=52.5, note="x"),
                directive(now, "mode", "m", mode="electric", request=True),
                directive(
                    now, "surplus_grant", "g", start=400, end=500, max_f=146
                ),
            ],
            plan_id="p",
        )
        intent = parse(document)

        again = parse(intent.as_document())

        assert again == intent
        stored = intent.as_document()
        assert stored["plan_id"] == "p"
        assert stored["directives"][0]["target_c"] == 52.5
        assert "target_f" not in stored["directives"][0]
        assert stored["directives"][2]["max_f"] == pytest.approx(146, abs=0.5)

    def test_is_stale(self, now, parse):
        intent = parse(make_document(now, valid_for=10))
        assert not intent.is_stale(now + timedelta(minutes=9))
        assert intent.is_stale(now + timedelta(minutes=10))


class TestRejectedDocuments:
    """Each rule in section 3.3 rejects the document whole."""

    @staticmethod
    def _rejects(parse, document, reason):
        with pytest.raises(IntentRejected) as exc_info:
            parse(document)
        assert exc_info.value.reason == reason
        return exc_info.value

    @pytest.mark.parametrize(
        "missing",
        ["protocol", "intent_id", "issued_at", "valid_until", "directives"],
    )
    def test_missing_required_key(self, now, parse, missing):
        document = make_document(now)
        del document[missing]
        self._rejects(parse, document, REASON_INVALID_DOCUMENT)

    def test_unsupported_protocol(self, now, parse):
        self._rejects(
            parse, make_document(now, protocol="1"), REASON_UNSUPPORTED_PROTOCOL
        )

    def test_protocol_minor_versions_are_accepted(self, now, parse):
        assert parse(make_document(now, protocol="0.3")).intent_id == "i-1"

    def test_protocol_must_be_a_string(self, now, parse):
        self._rejects(
            parse, make_document(now, protocol=0), REASON_INVALID_DOCUMENT
        )

    def test_intent_id_too_long(self, now, parse):
        self._rejects(
            parse,
            make_document(now, intent_id="x" * 65),
            REASON_INVALID_DOCUMENT,
        )

    def test_intent_id_must_be_a_non_empty_string(self, now, parse):
        self._rejects(
            parse, make_document(now, intent_id=""), REASON_INVALID_DOCUMENT
        )
        self._rejects(
            parse, make_document(now, intent_id=7), REASON_INVALID_DOCUMENT
        )

    def test_valid_until_not_after_issued_at(self, now, parse):
        document = make_document(now, valid_for=0)
        self._rejects(parse, document, REASON_INVALID_VALIDITY)

    def test_stale_on_receipt(self, now, parse):
        document = make_document(now)
        document["issued_at"] = ts(now, -120)
        document["valid_until"] = ts(now, -60)
        self._rejects(parse, document, REASON_STALE_ON_RECEIPT)

    def test_naive_timestamp(self, now, parse):
        document = make_document(now)
        document["issued_at"] = now.replace(tzinfo=None).isoformat()
        err = self._rejects(parse, document, REASON_INVALID_DOCUMENT)
        assert "offset" in err.detail

    def test_unparseable_timestamp(self, now, parse):
        document = make_document(now)
        document["valid_until"] = "tomorrow"
        self._rejects(parse, document, REASON_INVALID_DOCUMENT)

    def test_directives_must_be_a_list(self, now, parse):
        self._rejects(
            parse, make_document(now, directives={}), REASON_INVALID_DOCUMENT
        )

    def test_directive_must_be_an_object(self, now, parse):
        self._rejects(
            parse, make_document(now, ["charge"]), REASON_INVALID_DOCUMENT
        )

    @pytest.mark.parametrize("missing", ["id", "type", "start", "end"])
    def test_directive_missing_key(self, now, parse, missing):
        d = directive(now, "hold_off")
        del d[missing]
        self._rejects(parse, make_document(now, [d]), REASON_INVALID_DOCUMENT)

    def test_unknown_directive_type(self, now, parse):
        self._rejects(
            parse,
            make_document(now, [directive(now, "boost")]),
            REASON_INVALID_DOCUMENT,
        )

    def test_duplicate_directive_ids(self, now, parse):
        self._rejects(
            parse,
            make_document(
                now,
                [
                    directive(now, "hold_off", "same", start=0, end=60),
                    directive(now, "hold_off", "same", start=60, end=120),
                ],
            ),
            REASON_DUPLICATE_DIRECTIVE_ID,
        )

    def test_window_end_not_after_start(self, now, parse):
        self._rejects(
            parse,
            make_document(now, [directive(now, "hold_off", start=60, end=60)]),
            REASON_INVALID_WINDOW,
        )

    def test_charge_overlapping_hold_off(self, now, parse):
        self._rejects(
            parse,
            make_document(
                now,
                [
                    directive(now, "hold_off", "h", start=0, end=200),
                    directive(
                        now, "charge", "c", start=199, end=400, target_f=130
                    ),
                ],
            ),
            REASON_CHARGE_OVERLAPS_HOLD_OFF,
        )

    def test_adjacent_windows_do_not_overlap(self, now, parse):
        document = make_document(
            now,
            [
                directive(now, "hold_off", "h", start=0, end=200),
                directive(now, "charge", "c", start=200, end=400, target_f=130),
            ],
        )
        assert len(parse(document).directives) == 2

    def test_grant_overlapping_mode(self, now, parse):
        self._rejects(
            parse,
            make_document(
                now,
                [
                    directive(
                        now, "mode", "m", start=0, end=200, mode="electric"
                    ),
                    directive(
                        now, "surplus_grant", "g", start=100, end=300, max_f=146
                    ),
                ],
            ),
            REASON_GRANT_OVERLAPS_MODE,
        )

    def test_charge_needs_exactly_one_unit(self, now, parse):
        self._rejects(
            parse,
            make_document(now, [directive(now, "charge")]),
            REASON_INVALID_DOCUMENT,
        )
        self._rejects(
            parse,
            make_document(
                now, [directive(now, "charge", target_f=140, target_c=60)]
            ),
            REASON_INVALID_DOCUMENT,
        )

    def test_temperature_must_be_a_number(self, now, parse):
        self._rejects(
            parse,
            make_document(now, [directive(now, "charge", target_f="140")]),
            REASON_INVALID_DOCUMENT,
        )
        self._rejects(
            parse,
            make_document(now, [directive(now, "surplus_grant", max_c=True)]),
            REASON_INVALID_DOCUMENT,
        )

    @pytest.mark.parametrize("mode", ["vacation", "power_off", "eco", None])
    def test_mode_name(self, now, parse, mode):
        """Vacation and power-off are never accepted in a directive."""
        self._rejects(
            parse,
            make_document(now, [directive(now, "mode", mode=mode)]),
            REASON_INVALID_DOCUMENT,
        )

    def test_request_must_be_boolean(self, now, parse):
        self._rejects(
            parse,
            make_document(
                now, [directive(now, "mode", mode="heat_pump", request="yes")]
            ),
            REASON_INVALID_DOCUMENT,
        )


def test_document_from_attributes_drops_presentation_keys():
    attributes = {
        "friendly_name": "Water heater intent",
        "icon": "mdi:x",
        "protocol": "0",
        "plan_id": "p",
    }
    assert document_from_attributes(attributes) == {
        "protocol": "0",
        "plan_id": "p",
    }
