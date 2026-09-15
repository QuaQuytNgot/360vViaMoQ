"""Priority experiment intent; no MOQT frame is serialized here."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PriorityChange:
    track_id: str
    subscriber_priority: int
    requested_ts_ns: int
    correlation_id: str

    def validate(self) -> None:
        if not self.track_id or not self.correlation_id:
            raise ValueError("track_id and correlation_id are required")
        if not 0 <= self.subscriber_priority <= 255:
            raise ValueError("subscriber priority must be an unsigned 8-bit value")
