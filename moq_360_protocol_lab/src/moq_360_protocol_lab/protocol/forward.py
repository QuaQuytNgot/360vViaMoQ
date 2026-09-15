"""Forward-state intent; activation requires a verified adapter."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ForwardIntent:
    track_id: str
    enabled: bool
    requested_ts_ns: int
    correlation_id: str
