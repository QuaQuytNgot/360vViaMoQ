"""Protocol-neutral live source emulator.

An adapter is handed a planned object only at its fixed wall-clock release.
The module does not construct a speculative MOQT message.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Protocol

from .events import EventRecorder
from .models import Observation, PlannedObject
from .pacing import LivePacer
from .workload import deterministic_payload


class ObjectSender(Protocol):
    def __call__(self, item: PlannedObject, payload: bytes) -> None: ...


class LivePublisher:
    def __init__(self, pacer: LivePacer, recorder: EventRecorder, clock_domain_id: str, seed: str) -> None:
        self.pacer = pacer
        self.recorder = recorder
        self.clock_domain_id = clock_domain_id
        self.seed = seed

    def release(self, objects: Iterable[PlannedObject], sender: ObjectSender) -> None:
        """Release all tracks for a media timestamp from the same fixed anchor."""
        for item in sorted(objects, key=lambda value: (value.scheduled_publish_ts_ns, value.track_id, value.group_id, value.object_id)):
            pacing = self.pacer.wait_for(item.scheduled_publish_ts_ns)
            payload = deterministic_payload(self.seed, item.track_id, item.group_id, item.object_id, item.payload_bytes)
            sender(item, payload)
            self.recorder.append(Observation(
                event_type="published", observed_ts_ns=pacing.actual_ts_ns,
                clock_domain_id=self.clock_domain_id, run_id=item.run_id,
                track_id=item.track_id, tile_id=item.tile_id, group_id=item.group_id,
                object_id=item.object_id, payload_bytes=item.payload_bytes,
                provenance="application_release",
                details={"scheduled_publish_ts_ns": item.scheduled_publish_ts_ns, "pacer_lateness_ns": pacing.lateness_ns},
            ))
