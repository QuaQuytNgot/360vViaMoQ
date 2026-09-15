"""Deterministic synthetic workload planning with no implicit experiment values."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable, Iterator, Sequence

from .models import PlannedObject


@dataclass(frozen=True)
class SyntheticWorkload:
    track_ids: tuple[str, ...]
    tile_ids: tuple[str, ...]
    per_track_bitrate_bps: tuple[int, ...]
    group_duration_ns: int
    group_count: int
    objects_per_group: int
    seed: str

    def validate(self) -> None:
        if not self.track_ids:
            raise ValueError("synthetic workload needs at least one track")
        if len(self.track_ids) != len(self.tile_ids) or len(self.track_ids) != len(self.per_track_bitrate_bps):
            raise ValueError("track_ids, tile_ids, and per_track_bitrate_bps must have equal length")
        if len(set(self.track_ids)) != len(self.track_ids):
            raise ValueError("track IDs must be unique")
        if any(rate <= 0 for rate in self.per_track_bitrate_bps):
            raise ValueError("per-track bitrates must be positive")
        if self.group_duration_ns <= 0 or self.group_count <= 0 or self.objects_per_group <= 0:
            raise ValueError("group duration, group count, and objects per group must be positive")
        if not self.seed:
            raise ValueError("synthetic workload needs an explicit seed")


def deterministic_payload(seed: str, track_id: str, group_id: int, object_id: int, size: int) -> bytes:
    """Generate bytes lazily and deterministically; no file or codec dependence."""
    if size < 0:
        raise ValueError("payload size cannot be negative")
    material = f"{seed}|{track_id}|{group_id}|{object_id}".encode("utf-8")
    return hashlib.shake_256(material).digest(size)


def plan_synthetic_objects(workload: SyntheticWorkload, run_id: str, anchor_ts_ns: int) -> Iterator[PlannedObject]:
    """Yield one plan for each logical object using integer remainder accounting."""
    workload.validate()
    bytes_denominator = 8_000_000_000  # bits per second × nanoseconds per second
    for track_id, tile_id, rate_bps in zip(workload.track_ids, workload.tile_ids, workload.per_track_bitrate_bps):
        group_remainder = 0
        for group_id in range(workload.group_count):
            numerator = rate_bps * workload.group_duration_ns + group_remainder
            group_bytes, group_remainder = divmod(numerator, bytes_denominator)
            object_remainder = 0
            for object_id in range(workload.objects_per_group):
                object_numerator = group_bytes + object_remainder
                object_bytes, object_remainder = divmod(object_numerator, workload.objects_per_group)
                logical_time = group_id * workload.group_duration_ns
                yield PlannedObject(
                    run_id=run_id,
                    track_id=track_id,
                    tile_id=tile_id,
                    group_id=group_id,
                    object_id=object_id,
                    logical_media_time_ns=logical_time,
                    scheduled_publish_ts_ns=anchor_ts_ns + logical_time,
                    payload_bytes=object_bytes,
                    content_id=f"synthetic:{track_id}:{group_id}:{object_id}",
                )


def validate_track_mapping(track_ids: Sequence[str], tile_ids: Sequence[str]) -> None:
    if len(track_ids) != len(tile_ids):
        raise ValueError("each logical track must map to exactly one tile ID")
