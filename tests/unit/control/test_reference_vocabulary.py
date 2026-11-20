"""The reference names every status, reason, warning and kind reported.

A scheduler writes against the reference: a value the feature can report
that the reference never names is one no scheduler handles.
"""

from pathlib import Path

import pytest

from custom_components.nwp500.control.commands import CommandStatus
from custom_components.nwp500.control.engine import WriteReason
from custom_components.nwp500.control.entries import EntryKind
from custom_components.nwp500.control.evaluate import (
    AckState,
    ItemWarning,
    SegmentReason,
    SegmentStatus,
)
from custom_components.nwp500.control.intent import (
    CommandRejectReason,
    RejectReason,
)

REFERENCE = (
    Path(__file__).parents[3] / "docs" / "external-control-protocol-1.md"
).read_text()


@pytest.mark.parametrize(
    "vocabulary",
    [
        AckState,
        SegmentStatus,
        SegmentReason,
        ItemWarning,
        CommandStatus,
        EntryKind,
        RejectReason,
        CommandRejectReason,
        WriteReason,
    ],
)
def test_the_reference_names_every_value(vocabulary):
    missing = [v.value for v in vocabulary if f"`{v.value}`" not in REFERENCE]
    assert missing == []
