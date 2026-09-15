"""Metric derivation from planned objects and explicitly observed events."""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from .models import ObjectDelivery, PlannedObject
from .stats import percentile


OBJECT_COLUMNS = [
    "run_id", "user_id", "track_id", "tile_id", "group_id", "object_id",
    "scheduled_publish_ts_ns", "actual_publish_ts_ns", "first_receive_ts_ns",
    "complete_receive_ts_ns", "payload_bytes", "completed", "dropped", "expired",
    "reset", "out_of_order", "subscriber_priority", "publisher_priority", "provenance",
]


def normalize_deliveries(
    planned: Iterable[PlannedObject], events: Iterable[Mapping[str, object]], user_id: str,
) -> list[ObjectDelivery]:
    """Join events to expected objects; absent observations never imply success."""
    expected = {(item.track_id, item.group_id, item.object_id): item for item in planned}
    observed: dict[tuple[str, int, int], dict[str, object]] = defaultdict(dict)
    last_received: dict[str, tuple[int, int]] = {}
    for event in events:
        event_user = event.get("user_id")
        if event_user is not None and event_user != user_id:
            continue
        track_id, group_id, object_id = event.get("track_id"), event.get("group_id"), event.get("object_id")
        if not isinstance(track_id, str) or not isinstance(group_id, int) or not isinstance(object_id, int):
            continue
        key = (track_id, group_id, object_id)
        if key not in expected:
            continue
        event_type, timestamp = event.get("event_type"), event.get("observed_ts_ns")
        if not isinstance(timestamp, int):
            continue
        entry = observed[key]
        if event_type == "published":
            entry["actual_publish_ts_ns"] = timestamp
        elif event_type == "first_received":
            entry.setdefault("first_receive_ts_ns", timestamp)
            location = (group_id, object_id)
            previous = last_received.get(track_id)
            if previous is not None and location < previous:
                entry["out_of_order"] = True
            last_received[track_id] = max(previous or location, location)
        elif event_type == "completed":
            entry["complete_receive_ts_ns"] = timestamp
            entry["completed"] = True
        elif event_type in {"dropped", "expired", "reset"}:
            entry[str(event_type)] = True
    rows: list[ObjectDelivery] = []
    for key, item in sorted(expected.items()):
        data = observed[key]
        rows.append(ObjectDelivery(
            run_id=item.run_id, user_id=user_id, track_id=item.track_id, tile_id=item.tile_id,
            group_id=item.group_id, object_id=item.object_id,
            scheduled_publish_ts_ns=item.scheduled_publish_ts_ns,
            actual_publish_ts_ns=_int_or_none(data.get("actual_publish_ts_ns")),
            first_receive_ts_ns=_int_or_none(data.get("first_receive_ts_ns")),
            complete_receive_ts_ns=_int_or_none(data.get("complete_receive_ts_ns")),
            payload_bytes=item.payload_bytes, completed=bool(data.get("completed")),
            dropped=bool(data.get("dropped")), expired=bool(data.get("expired")), reset=bool(data.get("reset")),
            out_of_order=bool(data.get("out_of_order")), provenance="normalized_event_log",
        ))
    return rows


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) else None


def write_object_csv(path: Path, rows: Iterable[ObjectDelivery]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OBJECT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row.as_dict())


def group_completion_times(rows: Iterable[ObjectDelivery]) -> dict[tuple[str, str, int], int]:
    """Return a group time only if every expected object in that group completed."""
    grouped: dict[tuple[str, str, int], list[ObjectDelivery]] = defaultdict(list)
    for row in rows:
        grouped[(row.user_id, row.tile_id, row.group_id)].append(row)
    completed: dict[tuple[str, str, int], int] = {}
    for key, objects in grouped.items():
        if all(item.completed and item.complete_receive_ts_ns is not None for item in objects):
            completed[key] = max(item.complete_receive_ts_ns for item in objects if item.complete_receive_ts_ns is not None)
    return completed


def cross_tile_skew_ns(rows: Iterable[ObjectDelivery], required_tile_ids: Sequence[str]) -> dict[tuple[str, int], int | None]:
    """Skew is unavailable for incomplete tile sets; it is never coerced to zero."""
    rows = list(rows)
    complete = group_completion_times(rows)
    keys = {(row.user_id, row.group_id) for row in rows}
    result: dict[tuple[str, int], int | None] = {}
    for user_id, group_id in keys:
        timestamps = [complete.get((user_id, tile_id, group_id)) for tile_id in required_tile_ids]
        result[(user_id, group_id)] = None if any(value is None for value in timestamps) else max(timestamps) - min(timestamps)  # type: ignore[arg-type]
    return result


def viewport_completion_ns(rows: Iterable[ObjectDelivery], required_tile_ids: Sequence[str]) -> dict[tuple[str, int], int | None]:
    rows = list(rows)
    complete = group_completion_times(rows)
    keys = {(row.user_id, row.group_id) for row in rows}
    result: dict[tuple[str, int], int | None] = {}
    for user_id, group_id in keys:
        timestamps = [complete.get((user_id, tile_id, group_id)) for tile_id in required_tile_ids]
        result[(user_id, group_id)] = None if any(value is None for value in timestamps) else max(timestamps)  # type: ignore[arg-type]
    return result


def deadline_miss_ratio(rows: Iterable[ObjectDelivery], deadline_ns: int, observation_end_ts_ns: int | None = None) -> float | None:
    if deadline_ns < 0:
        raise ValueError("deadline_ns cannot be negative")
    evaluated = [
        row for row in rows
        if row.complete_receive_ts_ns is not None
        or (observation_end_ts_ns is not None and observation_end_ts_ns > row.scheduled_publish_ts_ns + deadline_ns)
    ]
    if not evaluated:
        return None
    misses = sum(
        row.complete_receive_ts_ns is None
        or row.complete_receive_ts_ns - row.scheduled_publish_ts_ns > deadline_ns
        for row in evaluated
    )
    return misses / len(evaluated)


def missing_tiles_per_group(rows: Iterable[ObjectDelivery], required_tile_ids: Sequence[str]) -> dict[tuple[str, int], int]:
    """Count missing required tiles at each media group, including wholly absent tiles."""
    rows = list(rows)
    expected_groups = {(row.user_id, row.group_id) for row in rows}
    complete = group_completion_times(rows)
    return {
        key: sum(complete.get((key[0], tile_id, key[1])) is None for tile_id in required_tile_ids)
        for key in expected_groups
    }


def group_completion_ratio(rows: Iterable[ObjectDelivery], required_tile_ids: Sequence[str]) -> float | None:
    missing = missing_tiles_per_group(rows, required_tile_ids)
    if not missing:
        return None
    return sum(value == 0 for value in missing.values()) / len(missing)


def goodput_bps(rows: Iterable[ObjectDelivery], start_ts_ns: int, end_ts_ns: int) -> dict[tuple[str, str], float]:
    """Unique payload bytes completed inside a declared observation interval."""
    if end_ts_ns <= start_ts_ns:
        raise ValueError("goodput interval must have positive duration")
    interval_s = (end_ts_ns - start_ts_ns) / 1_000_000_000
    delivered: dict[tuple[str, str], int] = defaultdict(int)
    seen: set[tuple[str, str, int, int]] = set()
    for row in rows:
        key = (row.user_id, row.track_id, row.group_id, row.object_id)
        if key in seen or not row.completed or row.complete_receive_ts_ns is None:
            continue
        if start_ts_ns <= row.complete_receive_ts_ns <= end_ts_ns:
            delivered[(row.user_id, row.track_id)] += row.payload_bytes
            seen.add(key)
    return {track_id: (payload * 8) / interval_s for track_id, payload in delivered.items()}


def p1_summary(rows: Iterable[ObjectDelivery], required_tile_ids: Sequence[str], start_ts_ns: int, end_ts_ns: int, deadline_ns: int | None = None) -> dict[str, object]:
    """Compute P1 outcomes without assigning an interpretation to fairness.

    Completion latency is object completion time minus its scheduled source
    release time.  It is available only for completed objects; absent objects
    remain separately visible in object/group completion metrics.
    """
    materialized = list(rows)
    per_track = goodput_bps(materialized, start_ts_ns, end_ts_ns)
    latencies = [
        row.complete_receive_ts_ns - row.scheduled_publish_ts_ns
        for row in materialized
        if row.completed and row.complete_receive_ts_ns is not None
    ]
    result: dict[str, object] = {
        "aggregate_goodput_bps": sum(per_track.values()),
        "per_track_goodput_bps": {f"{user}/{track}": value for (user, track), value in sorted(per_track.items())},
        "weakest_track_goodput_bps": min(per_track.values()) if per_track else None,
        "group_completion_ratio": group_completion_ratio(materialized, required_tile_ids),
        "object_completion_ratio": (
            sum(row.completed for row in materialized) / len(materialized)
            if materialized else None
        ),
        "cross_tile_completion_skew_ns": cross_tile_skew_ns(materialized, required_tile_ids),
        "completion_latency_ns": {
            "count": len(latencies),
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "p99": percentile(latencies, 0.99),
        },
    }
    if deadline_ns is not None:
        result["deadline_miss_ratio"] = deadline_miss_ratio(materialized, deadline_ns, end_ts_ns)
    return result
