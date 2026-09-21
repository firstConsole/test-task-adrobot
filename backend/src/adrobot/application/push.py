"""What one press of PUSH TO KT ended in, as the audit table records it."""

from __future__ import annotations

from enum import Enum


class PushOutcome(Enum):
    """Each value says what the tracker is holding now.

    `INDETERMINATE` is the one that earns its place: the request left and no answer came,
    so the write may or may not have landed, and the way out is a read rather than a blind
    retry. `MISMATCHED` is the flow reading back as something other than what was written.
    """

    IN_FLIGHT = "in_flight"
    APPLIED = "applied"
    CONFLICT = "conflict"
    FAILED = "failed"
    INDETERMINATE = "indeterminate"
    MISMATCHED = "mismatched"
