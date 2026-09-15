"""Wall-clock live-source pacing using a fixed monotonic anchor."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable


Clock = Callable[[], int]
Sleeper = Callable[[float], None]


@dataclass(frozen=True)
class PacingResult:
    scheduled_ts_ns: int
    actual_ts_ns: int

    @property
    def lateness_ns(self) -> int:
        return self.actual_ts_ns - self.scheduled_ts_ns


class LivePacer:
    """Release at an absolute schedule; never derive a later target from sleep.

    `actual_ts_ns` means the application released the object to its adapter.
    It is not claimed to be a transport on-wire send time.
    """

    def __init__(self, anchor_ts_ns: int | None = None, *, clock: Clock = time.monotonic_ns, sleeper: Sleeper = time.sleep) -> None:
        self._clock = clock
        self._sleep = sleeper
        self.anchor_ts_ns = clock() if anchor_ts_ns is None else anchor_ts_ns

    def scheduled_time(self, logical_media_time_ns: int) -> int:
        if logical_media_time_ns < 0:
            raise ValueError("logical_media_time_ns must be non-negative")
        return self.anchor_ts_ns + logical_media_time_ns

    def wait_for(self, scheduled_ts_ns: int) -> PacingResult:
        remaining_ns = scheduled_ts_ns - self._clock()
        if remaining_ns > 0:
            self._sleep(remaining_ns / 1_000_000_000)
        return PacingResult(scheduled_ts_ns=scheduled_ts_ns, actual_ts_ns=self._clock())
