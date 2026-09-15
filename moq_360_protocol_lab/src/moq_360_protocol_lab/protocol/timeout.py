"""Draft-18 timeout intent and native-observation classification.

No local clock-driven expiry is implemented here.  A result is classified as a
delivery timeout only when a future native adapter supplies an explicit native
source and ``delivery_timeout`` reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class DeliveryTimeoutIntent:
    track_id: str
    object_delivery_timeout_ms: int | None = None
    subgroup_delivery_timeout_ms: int | None = None

    def validate(self) -> None:
        if not self.track_id:
            raise ValueError("track_id is required")
        values = (self.object_delivery_timeout_ms, self.subgroup_delivery_timeout_ms)
        if all(value is None for value in values):
            raise ValueError("at least one delivery timeout must be explicitly selected")
        if any(value is not None and value < 0 for value in values):
            raise ValueError("timeouts cannot be negative")


def classify_timeout_event(event: Mapping[str, object]) -> str:
    """Classify a native timeout/reset observation conservatively.

    ``application_deadline`` and generic dropped events remain unclassified;
    treating them as MOQT delivery-timeout evidence would be a false claim.
    """
    source = event.get("source")
    native_source = source in {"native_moqt", "native_relay"}
    if not native_source or event.get("reason") != "delivery_timeout":
        return "unclassified"
    event_type = event.get("event_type")
    if event_type == "reset":
        return "native_delivery_timeout_reset"
    if event_type == "expired":
        return "native_delivery_timeout_expired"
    if event_type == "dropped":
        return "native_delivery_timeout_drop"
    return "unclassified"
