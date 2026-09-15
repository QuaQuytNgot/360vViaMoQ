"""Draft-18 FETCH/Joining-Fetch experiment intent, without wire encoding."""

from __future__ import annotations

from dataclasses import dataclass

from .filters import LocationSelection


@dataclass(frozen=True)
class JoinIntent:
    track_id: str
    switch_ts_ns: int
    selection: LocationSelection
    correlation_id: str = ""

    def validate(self) -> None:
        if not self.track_id or not self.correlation_id:
            raise ValueError("track_id and correlation_id are required")
        if self.switch_ts_ns < 0:
            raise ValueError("switch_ts_ns cannot be negative")
        self.selection.validate()
