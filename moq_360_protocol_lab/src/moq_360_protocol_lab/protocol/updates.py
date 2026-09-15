"""Control-plane update records used to measure only observable behavior.

These records intentionally do not serialize a ``REQUEST_UPDATE``.  The
audited aiomoqt release lacks a conformant public sender for draft-18 updates
on the existing request bidi stream.  When that native path exists, an adapter
can use this module to keep control response and media reaction distinct.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class UpdateIntent:
    target_id: str
    requested_ts_ns: int
    correlation_id: str
    changes: Mapping[str, Any]

    def validate(self) -> None:
        if not self.target_id or not self.correlation_id:
            raise ValueError("target_id and correlation_id are required")
        if not self.changes:
            raise ValueError("an update must express at least one semantic change")


@dataclass(frozen=True)
class UpdateTiming:
    """Native observations for one priority update correlation ID.

    Values use the clock domain emitted by the adapter. ``None`` means the
    corresponding event was not observable; it never means zero latency.
    """

    update_generated_ts_ns: int
    request_update_sent_ts_ns: int | None = None
    update_response_ts_ns: int | None = None
    first_schedule_effect_ts_ns: int | None = None
    first_new_priority_object_ts_ns: int | None = None

    def validate(self) -> None:
        timestamps = asdict(self)
        if any(value is not None and (not isinstance(value, int) or value < 0) for value in timestamps.values()):
            raise ValueError("update timestamps must be non-negative integers or None")
        sent = self.request_update_sent_ts_ns
        if sent is not None and sent < self.update_generated_ts_ns:
            raise ValueError("REQUEST_UPDATE cannot be sent before it is generated")
        for key in ("update_response_ts_ns", "first_schedule_effect_ts_ns", "first_new_priority_object_ts_ns"):
            value = getattr(self, key)
            if value is not None and sent is not None and value < sent:
                raise ValueError(f"{key} cannot precede REQUEST_UPDATE send")


def update_timing_metrics(timing: UpdateTiming) -> dict[str, int | None]:
    """Return P2 timings without treating a response as a scheduling effect."""
    timing.validate()
    sent = timing.request_update_sent_ts_ns

    def since_send(observed: int | None) -> int | None:
        return observed - sent if observed is not None and sent is not None else None

    return {
        "update_generated_ts_ns": timing.update_generated_ts_ns,
        "request_update_sent_ts_ns": sent,
        "update_response_ts_ns": timing.update_response_ts_ns,
        "first_schedule_effect_ts_ns": timing.first_schedule_effect_ts_ns,
        "first_new_priority_object_ts_ns": timing.first_new_priority_object_ts_ns,
        "control_response_latency_ns": since_send(timing.update_response_ts_ns),
        "effective_scheduling_reaction_latency_ns": since_send(timing.first_schedule_effect_ts_ns),
        "new_priority_object_latency_ns": since_send(timing.first_new_priority_object_ts_ns),
    }
