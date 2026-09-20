"""Prepare, finalize, and aggregate native P5 measurements."""

from __future__ import annotations

import argparse
import csv
import json
import uuid
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import ConfigurationError, dump_yaml, load_yaml, nested_get, validate_for_run
from .events import read_events
from .experiment import _synthetic_workload, _write_json
from .manifest import read_manifest, write_manifest
from .p1_adapter import P1AdapterError
from .p4_runner import RESOURCE_ERROR_MARKERS, _counter, classify_relay_errors
from .p5_adapter import P5AdapterSettings
from .pacing import LivePacer
from .provenance import capture_provenance
from .workload import plan_synthetic_objects


P5_SUMMARY_FIELDS = [
    "run_id", "series", "mode", "join_offset_ms", "actual_join_offset_ms",
    "tracks", "rep", "first_object_ms", "first_complete_group_ms",
    "set_first_object_ms", "set_first_group_ms", "group_skew_ms",
    "historical_bytes", "redundant_bytes", "missing_objects",
    "live_edge_delay_ms", "valid",
]


def _run_id() -> str:
    return "run_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]


def prepare(config_path: Path, results_root: Path) -> tuple[Path, dict[str, Any]]:
    config = load_yaml(config_path)
    errors = validate_for_run(config)
    if errors:
        raise ConfigurationError("\n".join(errors))
    if nested_get(config, "runtime.mode") != "live" or nested_get(config, "experiment.test_id") != "P5":
        raise ConfigurationError("p5_runner requires a live P5 configuration")
    if (nested_get(config, "protocol.backend"), nested_get(config, "protocol.draft"),
            nested_get(config, "protocol.transport")) != ("moqt18", 18, "raw_quic"):
        raise ConfigurationError("p5_runner requires strict moqt18/raw_quic/draft 18")
    settings = P5AdapterSettings.from_config(config, namespace="validation-only")
    start_delay_ms = nested_get(config, "runtime.start_delay_ms")
    if not isinstance(start_delay_ms, int) or start_delay_ms < 1000:
        raise ConfigurationError("P5 runtime.start_delay_ms must be at least 1000")
    run_id = _run_id()
    run_dir = results_root / "P5" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    dump_yaml(run_dir / "config.yaml", config)
    anchor = LivePacer().anchor_ts_ns + start_delay_ms * 1_000_000
    workload = _synthetic_workload(config)
    interval_ns = int(nested_get(config, "workload.object_interval_ms")) * 1_000_000
    planned = [replace(item,
        logical_media_time_ns=item.group_id * workload.group_duration_ns + item.object_id * interval_ns,
        scheduled_publish_ts_ns=anchor + item.group_id * workload.group_duration_ns + item.object_id * interval_ns)
        for item in plan_synthetic_objects(workload, run_id, anchor)]
    write_manifest(run_dir / "manifest.jsonl", planned)
    plan = {
        "run_id": run_id, "source_anchor_ts_ns": anchor, "mode": settings.mode,
        "series": nested_get(config, "p5.series"), "join_offset_ms": nested_get(config, "p5.join_offset_ms"),
        "rep": nested_get(config, "p5.rep"), "track_count": len(workload.track_ids),
        "group_duration_ms": workload.group_duration_ns // 1_000_000,
        "object_interval_ms": interval_ns // 1_000_000,
    }
    _write_json(run_dir / "endpoint_plan.json", plan)
    return run_dir, {"run_id": run_id, "mode": settings.mode, "expected_objects": len(planned)}


def _write_event_csv(source: Path, destination: Path) -> None:
    fields = ["event_type", "observed_ts_ns", "track_id", "group_id", "object_id",
              "payload_bytes", "correlation_id", "native_operation", "details"]
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        if source.is_file():
            for event in read_events(source):
                writer.writerow({field: json.dumps(event.get(field), sort_keys=True)
                                 if field == "details" else event.get(field) for field in fields})


def _monitor_pass(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        rows = list(csv.DictReader(path.open(encoding="utf-8")))
    except OSError:
        return False
    return (bool(rows) and all(row.get("relay_alive") == "1" for row in rows)
            and any(row.get("publisher_alive") == "1" for row in rows)
            and any(row.get("subscriber_alive") == "1" for row in rows))


def _identity_records(subscriber: dict[str, Any]) -> list[dict[str, Any]]:
    records = subscriber.get("received_records", [])
    return records if isinstance(records, list) else []


def _group_rows(planned: list[Any], records: list[dict[str, Any]], demand: int) -> list[dict[str, Any]]:
    expected: dict[tuple[str, int], list[Any]] = defaultdict(list)
    received: dict[tuple[str, int], dict[int, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for item in planned:
        expected[(item.track_id, item.group_id)].append(item)
    for record in records:
        key = (record.get("track_id"), record.get("group_id"))
        object_id = record.get("object_id")
        if isinstance(key[0], str) and isinstance(key[1], int) and isinstance(object_id, int):
            received[key][object_id].append(record)
    rows = []
    for (track, group), items in sorted(expected.items()):
        by_object = received[(track, group)]
        unique = {object_id: min(entries, key=lambda row: row["received_ts_ns"])
                  for object_id, entries in by_object.items()}
        timestamps = [row["received_ts_ns"] for row in unique.values()]
        sources = {entry["source"] for entries in by_object.values() for entry in entries}
        delivery_source = "mixed" if len(sources) > 1 else next(iter(sources), "none")
        complete = len(unique) == len(items)
        rows.append({
            "track_id": track, "group_id": group,
            "source_start_ts": min(item.scheduled_publish_ts_ns for item in items),
            "expected_objects": len(items), "received_objects": len(unique),
            "first_object_ts": min(timestamps) if timestamps else None,
            "last_object_ts": max(timestamps) if timestamps else None,
            "complete": complete, "delivery_source": delivery_source,
            "completion_ts_ns": max(timestamps) if complete else None,
            "after_demand": bool(timestamps and max(timestamps) >= demand),
        })
    return rows


def _first_complete_by_track(groups: list[dict[str, Any]], demand: int) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in groups:
        completion = row.get("completion_ts_ns")
        if row["complete"] and isinstance(completion, int) and completion >= demand:
            current = result.get(row["track_id"])
            if current is None or completion < current["completion_ts_ns"]:
                result[row["track_id"]] = row
    return result


def _first_complete_set(groups: list[dict[str, Any]], tracks: list[str],
                        demand: int) -> dict[str, dict[str, Any]]:
    """Return the earliest common complete Group across every required Track."""
    candidates: list[tuple[int, dict[str, dict[str, Any]]]] = []
    for group_id in sorted({row["group_id"] for row in groups}):
        by_track = {row["track_id"]: row for row in groups
                    if row["group_id"] == group_id and row["complete"]
                    and isinstance(row.get("completion_ts_ns"), int)
                    and row["completion_ts_ns"] >= demand}
        if all(track in by_track for track in tracks):
            candidates.append((max(by_track[track]["completion_ts_ns"] for track in tracks), by_track))
    return min(candidates, key=lambda item: item[0])[1] if candidates else {}


def _write_groups(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = ["track_id", "group_id", "source_start_ts", "expected_objects", "received_objects",
              "first_object_ts", "last_object_ts", "complete", "delivery_source", "completion_ts_ns"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fields})


def _transition_rows(planned: list[Any], records: list[dict[str, Any]], demand: int,
                     first_complete: dict[str, dict[str, Any]],
                     unexpected: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    indexed: dict[tuple[str, int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        indexed[(row["track_id"], row["group_id"], row["object_id"])].append(row)
    rows = []
    for item in planned:
        boundary = first_complete.get(item.track_id)
        if boundary is None:
            continue
        demand_group = max(candidate.group_id for candidate in planned
                           if candidate.track_id == item.track_id and candidate.scheduled_publish_ts_ns <= demand)
        if not demand_group <= item.group_id <= boundary["group_id"]:
            continue
        deliveries = indexed[(item.track_id, item.group_id, item.object_id)]
        sources = {delivery["source"] for delivery in deliveries}
        if sources == {"fetch"}:
            classification = "fetched_only"
        elif sources == {"live"}:
            classification = "live_only"
        elif sources == {"fetch", "live"}:
            classification = "fetched_then_live_duplicate"
        else:
            classification = "missing"
        rows.append({"track_id": item.track_id, "group_id": item.group_id,
                     "object_id": item.object_id, "classification": classification,
                     "delivery_count": len(deliveries),
                     "first_receive_ts_ns": min((row["received_ts_ns"] for row in deliveries), default=None),
                     "payload_bytes": item.payload_bytes})
    for row in unexpected or []:
        rows.append({"track_id": row.get("track_id"), "group_id": row.get("group_id"),
                     "object_id": row.get("object_id"), "classification": "unexpected",
                     "delivery_count": 1, "first_receive_ts_ns": row.get("received_ts_ns"),
                     "payload_bytes": row.get("payload_bytes")})
    return rows


def _write_rows(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _live_edge_delay_ms(planned: list[Any], records: list[dict[str, Any]], at_ts: int) -> float | None:
    received_keys = {(row["track_id"], row["group_id"], row["object_id"])
                     for row in records if row["received_ts_ns"] <= at_ts}
    delays = []
    for track in sorted({item.track_id for item in planned}):
        source = [item.logical_media_time_ns for item in planned
                  if item.track_id == track and item.scheduled_publish_ts_ns <= at_ts]
        received = [item.logical_media_time_ns for item in planned
                    if item.track_id == track
                    and (item.track_id, item.group_id, item.object_id) in received_keys]
        if not source or not received:
            return None
        delays.append(max(0, max(source) - max(received)))
    return max(delays) / 1_000_000 if delays else None


def _append_summary(root: Path, row: dict[str, Any]) -> None:
    path = root / "P5" / "p5_summary.csv"
    exists = path.is_file()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=P5_SUMMARY_FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerow({field: row.get(field) for field in P5_SUMMARY_FIELDS})


def finalize(run_dir: Path) -> tuple[Path, dict[str, Any]]:
    run_dir = run_dir.resolve()
    config = load_yaml(run_dir / "config.yaml")
    plan = json.loads((run_dir / "endpoint_plan.json").read_text(encoding="utf-8"))
    settings = P5AdapterSettings.from_config(config, namespace=f"moq-360-p5/{plan['run_id']}")
    failures = [path for path in (run_dir / "publisher_failure.json", run_dir / "subscriber_failure.json") if path.exists()]
    if failures or not (run_dir / "publisher_result.json").is_file() or not (run_dir / "subscriber_result.json").is_file():
        raise ConfigurationError(f"P5 endpoint evidence incomplete: {failures}")
    publisher = json.loads((run_dir / "publisher_result.json").read_text(encoding="utf-8"))
    subscriber = json.loads((run_dir / "subscriber_result.json").read_text(encoding="utf-8"))
    _write_json(run_dir / "protocol_negotiation_publisher.json", publisher["protocol_negotiation_publisher"])
    _write_json(run_dir / "protocol_negotiation_subscriber.json", subscriber["protocol_negotiation_subscriber"])
    for name in ("publisher", "subscriber", "fetch_events", "subscribe_events", "object_transition"):
        _write_event_csv(run_dir / f"{name}.events.jsonl", run_dir / f"{name}.csv")

    planned = list(read_manifest(run_dir / "manifest.jsonl"))
    track_ids = sorted({item.track_id for item in planned})
    records = _identity_records(subscriber)
    demand = int(subscriber["demand_event_ts_ns"])
    groups = _group_rows(planned, records, demand)
    _write_groups(run_dir / "groups.csv", groups)
    first_complete = _first_complete_by_track(groups, demand)
    first_complete_set = _first_complete_set(groups, track_ids, demand)
    transitions = _transition_rows(planned, records, demand, first_complete,
                                   subscriber.get("unexpected_records", []))
    _write_rows(run_dir / "object_transition.csv", transitions,
        ["track_id", "group_id", "object_id", "classification", "delivery_count",
         "first_receive_ts_ns", "payload_bytes"])

    first_objects = {track: min((row["received_ts_ns"] for row in records
                                 if row["track_id"] == track and row["received_ts_ns"] >= demand), default=None)
                     for track in track_ids}
    completions = {track: row["completion_ts_ns"] for track, row in first_complete_set.items()}
    set_first_object = (max(first_objects.values())
                        if all(isinstance(value, int) for value in first_objects.values()) else None)
    set_first_group = max(completions.values()) if completions else None
    first_object = min((value for value in first_objects.values() if isinstance(value, int)), default=None)
    first_group = min(completions.values(), default=None)
    historical_bytes = sum(row["payload_bytes"] for row in records if row["source"] == "fetch")
    identities = Counter((row["track_id"], row["group_id"], row["object_id"]) for row in records)
    redundant_bytes = sum((count - 1) * next(item.payload_bytes for item in planned
                          if (item.track_id, item.group_id, item.object_id) == identity)
                          for identity, count in identities.items() if count > 1)
    missing_objects = sum(row["classification"] == "missing" for row in transitions)
    fetch_sent = list(read_events(run_dir / "fetch_events.events.jsonl")) if (run_dir / "fetch_events.events.jsonl").is_file() else []
    subscribe_sent = list(read_events(run_dir / "subscribe_events.events.jsonl")) if (run_dir / "subscribe_events.events.jsonl").is_file() else []
    phases = list(read_events(run_dir / "run_phases.events.jsonl")) if (run_dir / "run_phases.events.jsonl").is_file() else []
    actual_offset_ms = (demand - int(plan["source_anchor_ts_ns"])) / 1_000_000 % int(plan["group_duration_ms"])
    representative_ts = set_first_group if isinstance(set_first_group, int) else int(subscriber["measurement_end_ts_ns"])
    live_edge_delay = _live_edge_delay_ms(planned, records, representative_ts)

    relay_path = run_dir / "relay.log"
    relay_log = relay_path.read_text(encoding="utf-8", errors="replace") if relay_path.is_file() else ""
    teardown_started = any(event.get("details", {}).get("phase") == "ENDPOINT_TEARDOWN_START"
                           for event in phases)
    relay_errors = classify_relay_errors(relay_log, teardown_started=teardown_started)
    protocols_ok = (publisher["protocol_negotiation_publisher"].get("success") is True
                    and subscriber["protocol_negotiation_subscriber"].get("success") is True)
    payload_ok = subscriber.get("malformed_objects") == 0 and subscriber.get("unexpected_objects") == 0
    fetch_required = settings.mode != "live_only"
    native_fetch_ok = (not fetch_required or
        bool(records and any(row["source"] == "fetch" for row in records))
        and len([event for event in fetch_sent if event.get("event_type") == "fetch_sent"]) == len(first_objects)
        and len([event for event in fetch_sent if event.get("event_type") == "fetch_ok"]) == len(first_objects)
        and len(subscriber.get("fetch_done", {})) == len(track_ids)
        and all(subscriber.get("fetch_done", {}).values()))
    subscribe_required = settings.mode != "standalone_fetch_smoke"
    native_subscribe_ok = (not subscribe_required or
        len([event for event in subscribe_sent if event.get("event_type") == "subscribe_sent"]) == len(track_ids)
        and len([event for event in subscribe_sent if event.get("event_type") == "subscribe_ok"]) == len(track_ids))
    continuity_ok = (len(first_complete_set) == len(track_ids)
                     and set_first_object is not None
                     and (settings.mode == "live_only" or missing_objects == 0))
    monitor_ok = _monitor_pass(run_dir / "monitor.csv") and (run_dir / "monitor.jsonl").is_file()
    _write_json(run_dir / "provenance.json", capture_provenance(
        Path.cwd(), run_dir / "config.yaml", Path("configs/capabilities.example.json"),
        config["protocol"], config["relay"], "draft18_native"))
    valid = bool(protocols_ok and payload_ok and native_fetch_ok and native_subscribe_ok
                 and continuity_ok and monitor_ok
                 and relay_path.is_file() and not relay_errors["resource_errors"]
                 and not relay_errors["active_window_errors"] and (run_dir / "provenance.json").is_file())
    summary_row = {
        "run_id": plan["run_id"], "series": plan.get("series"), "mode": settings.mode,
        "join_offset_ms": plan.get("join_offset_ms"), "actual_join_offset_ms": actual_offset_ms,
        "tracks": plan["track_count"], "rep": plan.get("rep"),
        "first_object_ms": (first_object - demand) / 1_000_000 if isinstance(first_object, int) else None,
        "first_complete_group_ms": (first_group - demand) / 1_000_000 if isinstance(first_group, int) else None,
        "set_first_object_ms": (set_first_object - demand) / 1_000_000 if isinstance(set_first_object, int) else None,
        "set_first_group_ms": (set_first_group - demand) / 1_000_000 if isinstance(set_first_group, int) else None,
        "group_skew_ms": ((max(completions.values()) - min(completions.values())) / 1_000_000
                          if len(completions) > 1 else 0.0 if completions else None),
        "historical_bytes": historical_bytes, "redundant_bytes": redundant_bytes,
        "missing_objects": missing_objects, "live_edge_delay_ms": live_edge_delay, "valid": valid,
    }
    validity = {
        "protocol": "pass" if protocols_ok else "fail",
        "payload_integrity": "pass" if payload_ok else "fail",
        "native_fetch": "pass" if native_fetch_ok else "fail",
        "native_subscribe": "pass" if native_subscribe_ok else "fail",
        "continuity": "pass" if continuity_ok else "fail",
        "monitoring": "pass" if monitor_ok else "fail",
        "relay_log_scoped": "pass" if relay_path.is_file() else "fail",
        "relay_resource": "pass" if not relay_errors["resource_errors"] else "fail",
        "active_window_errors": "pass" if not relay_errors["active_window_errors"] else "fail",
    }
    summary = {**summary_row, "test_id": "P5", "status": "COMPLETED" if valid else "COMPLETED_INVALID",
        "valid_for_protocol_claim": valid, "validity": validity, "relay_errors": relay_errors,
        "subscribe_request_count": len([event for event in subscribe_sent if event.get("event_type") == "subscribe_sent"]),
        "fetch_request_count": len([event for event in fetch_sent if event.get("event_type") == "fetch_sent"]),
        "object_classification": dict(Counter(row["classification"] for row in transitions)),
        "live_transition_ts_ns": min((row["received_ts_ns"] for row in records
                                      if row["source"] == "live"), default=None),
        "first_complete_group_by_track": first_complete,
        "first_common_complete_group_by_track": first_complete_set,
        "live_edge_definition": "At the first common complete-Group time, for each required Track subtract its latest received logical media time from its latest source-available logical media time (scheduled_publish_ts_ns <= t); report the maximum non-negative Track delay.",
        "evaluated_protocol": "draft-ietf-moq-transport-18",
        "relay_cache": {"configured_max_groups_per_track": 3, "configured_max_tracks": 100,
                        "default_max_cached_mb": 16, "default_ttl_seconds": 86400},
    }
    _write_json(run_dir / "summary.json", summary)
    _append_summary(run_dir.parent.parent, summary_row)
    return run_dir, summary


def write_network_counters(run_dir: Path) -> None:
    entries = []
    for boundary, before_name, after_name, direction in (
        ("publisher_to_relay_tx", "network_publisher_before.json", "network_publisher_after.json", "tx"),
        ("relay_to_subscriber_tx", "network_relay_sub_before.json", "network_relay_sub_after.json", "tx"),
    ):
        before, after = _counter(run_dir / before_name, direction), _counter(run_dir / after_name, direction)
        entries.append({"boundary": boundary, "before_bytes": before, "after_bytes": after,
                        "delta_bytes": after - before if before is not None and after is not None else None,
                        "source": "ip -j -s link"})
    _write_rows(run_dir / "network_counters.csv", entries,
                ["boundary", "before_bytes", "after_bytes", "delta_bytes", "source"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare/finalize a native P5 run")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prepare", action="store_true")
    group.add_argument("--finalize", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--results", type=Path, default=Path("results"))
    args = parser.parse_args(argv)
    try:
        if args.prepare:
            if args.config is None:
                raise ConfigurationError("--prepare requires --config")
            run_dir, summary = prepare(args.config, args.results)
        else:
            assert args.finalize is not None
            write_network_counters(args.finalize)
            run_dir, summary = finalize(args.finalize)
    except (ConfigurationError, P1AdapterError) as exc:
        parser.error(str(exc))
    print(json.dumps({"run_dir": str(run_dir), **summary}, sort_keys=True))
    return 0 if args.prepare or summary["valid_for_protocol_claim"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
