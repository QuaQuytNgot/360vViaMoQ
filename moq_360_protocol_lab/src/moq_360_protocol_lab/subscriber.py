"""Receive-side normalization helpers; raw receipt is not decoder proof."""

from __future__ import annotations

import time
from dataclasses import dataclass

from .events import EventRecorder
from .models import Observation, PlannedObject


@dataclass
class SubscriberRecorder:
    user_id: str
    recorder: EventRecorder
    clock_domain_id: str

    def first_received(self, planned: PlannedObject, payload_bytes: int, *, native_operation: str | None = None) -> None:
        self.recorder.append(Observation(
            event_type="first_received", observed_ts_ns=time.monotonic_ns(), clock_domain_id=self.clock_domain_id,
            run_id=planned.run_id, user_id=self.user_id, track_id=planned.track_id, tile_id=planned.tile_id,
            group_id=planned.group_id, object_id=planned.object_id, payload_bytes=payload_bytes,
            provenance="subscriber_application", native_operation=native_operation,
        ))

    def completed(self, planned: PlannedObject, payload_bytes: int, *, native_operation: str | None = None) -> None:
        self.recorder.append(Observation(
            event_type="completed", observed_ts_ns=time.monotonic_ns(), clock_domain_id=self.clock_domain_id,
            run_id=planned.run_id, user_id=self.user_id, track_id=planned.track_id, tile_id=planned.tile_id,
            group_id=planned.group_id, object_id=planned.object_id, payload_bytes=payload_bytes,
            provenance="subscriber_application", native_operation=native_operation,
            details={"decoder_safe_metadata": planned.decoder_safe},
        ))

    def terminal_status(self, event_type: str, planned: PlannedObject, details: dict | None = None) -> None:
        if event_type not in {"dropped", "expired", "reset"}:
            raise ValueError("terminal status must be dropped, expired, or reset")
        self.recorder.append(Observation(
            event_type=event_type, observed_ts_ns=time.monotonic_ns(), clock_domain_id=self.clock_domain_id,
            run_id=planned.run_id, user_id=self.user_id, track_id=planned.track_id, tile_id=planned.tile_id,
            group_id=planned.group_id, object_id=planned.object_id, provenance="subscriber_application",
            details=details or {},
        ))
