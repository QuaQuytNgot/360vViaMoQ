"""Prepare, finalize, and summarize native P4 Forward-state measurements."""

from __future__ import annotations

import argparse
import csv
import json
import uuid
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .config import ConfigurationError, dump_yaml, load_yaml, nested_get, validate_for_run
from .events import read_events
from .experiment import _prepare_result_tables, _synthetic_workload, _write_json
from .manifest import read_manifest, write_manifest
from .metrics import group_completion_times, normalize_deliveries, write_object_csv
from .p1_adapter import P1AdapterError
from .p1_runner import _json_ready, _write_publisher_csv
from .p4_adapter import P4AdapterSettings
from .pacing import LivePacer
from .provenance import capture_provenance
from .workload import plan_synthetic_objects


def _run_id() -> str:
    return "run_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]


def prepare(config_path: Path, results_root: Path) -> tuple[Path, dict[str, Any]]:
    config = load_yaml(config_path)
    errors = validate_for_run(config)
    if errors:
        raise ConfigurationError("\n".join(errors))
    if nested_get(config, "runtime.mode") != "live" or nested_get(config, "experiment.test_id") != "P4":
        raise ConfigurationError("p4_runner requires a live P4 configuration")
    if (nested_get(config, "protocol.backend"), nested_get(config, "protocol.draft"), nested_get(config, "protocol.transport")) != ("moqt18", 18, "raw_quic"):
        raise ConfigurationError("p4_runner requires strict moqt18/raw_quic/draft 18")
    settings = P4AdapterSettings.from_config(config, namespace="validation-only")
    start_delay_ms = nested_get(config, "runtime.start_delay_ms")
    if not isinstance(start_delay_ms, int) or start_delay_ms < 0:
        raise ConfigurationError("runtime.start_delay_ms must be non-negative")
    run_id = _run_id()
    # Generate once; never replace a raw directory.
    run_dir = results_root / "P4" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    _prepare_result_tables(run_dir)
    dump_yaml(run_dir / "config.yaml", config)
    anchor = LivePacer().anchor_ts_ns + start_delay_ms * 1_000_000
    workload = _synthetic_workload(config)
    planned = list(plan_synthetic_objects(workload, run_id, anchor))
    write_manifest(run_dir / "manifest.jsonl", planned)
    _write_json(run_dir / "endpoint_plan.json", {"run_id": run_id, "source_anchor_ts_ns": anchor,
        "operation": nested_get(config, "runtime.operation"), "mode": settings.mode,
        "users": [u.user_id for u in settings.users], "publisher_namespace": "moq-p2-pub", "subscriber_namespace": "moq-p2-sub"})
    return run_dir, {"run_id": run_id, "mode": settings.mode, "expected_objects": len(planned), "users": len(settings.users)}


def _first_after(events: list[dict[str, Any]], tracks: set[str], demand: int | None) -> int | None:
    if demand is None:
        return None
    return min((e["observed_ts_ns"] for e in events if e.get("event_type") == "completed" and e.get("track_id") in tracks
                and isinstance(e.get("observed_ts_ns"), int) and e["observed_ts_ns"] >= demand), default=None)


def _counter(path: Path, direction: str) -> int | None:
    """Read one `ip -j -s link` snapshot written by the root-run script."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        value = data[0]["stats64"][direction]["bytes"]
    except (IndexError, KeyError, TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, int) else None


RESOURCE_ERROR_MARKERS = (
    "Failed to create uni stream", "CrossExecFilter beginSubgroup failed",
    "stream exhaustion", "file descriptor", "FD exhaustion", "socket allocation failure",
)


def classify_relay_errors(relay_log: str, *, teardown_started: bool) -> dict[str, list[str]]:
    """Separate always-fatal resource failures from lifecycle-scoped warnings.

    The scoped relay excerpt is captured only for this run.  moqx currently
    emits ``requestUpdate failed: Session closed`` after intentional endpoint
    close; it is a warning only when a teardown phase was recorded.
    """
    result: dict[str, list[str]] = {"resource_errors": [], "active_window_errors": [], "teardown_warnings": []}
    for line in relay_log.splitlines():
        if any(marker.lower() in line.lower() for marker in RESOURCE_ERROR_MARKERS):
            result["resource_errors"].append(line)
        elif "requestUpdate failed: Session closed" in line or "Session closed" in line and "requestUpdate failed" in line:
            (result["teardown_warnings"] if teardown_started else result["active_window_errors"]).append(line)
        elif "requestUpdate failed:" in line or "REQUEST_ERROR" in line or "PROTOCOL_VIOLATION" in line:
            result["active_window_errors"].append(line)
    return result


def p4_group_rows(planned: list[Any], events: list[dict[str, Any]], user_id: str, expected_forward: int) -> list[dict[str, Any]]:
    """Derive complete Groups strictly from all expected synthetic Objects."""
    planned_groups: dict[tuple[str, int], list[Any]] = defaultdict(list)
    for item in planned:
        planned_groups[(item.track_id, item.group_id)].append(item)
    received: dict[tuple[str, int, int], list[int]] = defaultdict(list)
    for event in events:
        if event.get("user_id") != user_id or event.get("event_type") != "completed":
            continue
        key = (event.get("track_id"), event.get("group_id"), event.get("object_id"))
        ts = event.get("observed_ts_ns")
        if isinstance(key[0], str) and isinstance(key[1], int) and isinstance(key[2], int) and isinstance(ts, int):
            received[key].append(ts)
    rows: list[dict[str, Any]] = []
    for (track, group), items in sorted(planned_groups.items()):
        item_by_id = {item.object_id: item for item in items}
        timestamps = {object_id: min(received[(track, group, object_id)]) for object_id in item_by_id if received[(track, group, object_id)]}
        complete = len(timestamps) == len(item_by_id)
        completion = max(timestamps.values()) if complete else None
        scheduled = min(item.scheduled_publish_ts_ns for item in items)
        rows.append({"run_id": items[0].run_id, "user_id": user_id, "track_id": track, "group_id": group,
            "scheduled_publish_ts_ns": scheduled, "first_object_receive_ts_ns": min(timestamps.values()) if timestamps else None,
            "last_object_receive_ts_ns": max(timestamps.values()) if timestamps else None,
            "expected_object_count": len(item_by_id), "received_object_count": len(timestamps), "complete": complete,
            "completion_ts_ns": completion, "completion_latency_ms": (completion - scheduled) / 1_000_000 if completion is not None else None,
            "expected_forward": expected_forward})
    return rows


def _monitor_pass(run_dir: Path) -> bool:
    path = run_dir / "monitor.csv"
    if not path.is_file():
        return False
    try:
        rows = list(csv.DictReader(path.open(encoding="utf-8")))
    except OSError:
        return False
    return (bool(rows) and all(row.get("relay_alive") == "1" for row in rows)
            and any(row.get("publisher_alive") == "1" for row in rows)
            and any(row.get("subscriber_alive") == "1" for row in rows))


def finalize(run_dir: Path) -> tuple[Path, dict[str, Any]]:
    run_dir = run_dir.resolve()
    config = load_yaml(run_dir / "config.yaml")
    plan = json.loads((run_dir / "endpoint_plan.json").read_text(encoding="utf-8"))
    settings = P4AdapterSettings.from_config(config, namespace=f"moq-360-p4/{plan['run_id']}")
    failures = [p for p in (run_dir / "publisher_failure.json", run_dir / "subscriber_failure.json") if p.exists()]
    if failures or not (run_dir / "publisher_result.json").is_file() or not (run_dir / "subscriber_result.json").is_file():
        raise ConfigurationError(f"P4 endpoint evidence incomplete: {failures}")
    publisher = json.loads((run_dir / "publisher_result.json").read_text(encoding="utf-8"))
    subscriber = json.loads((run_dir / "subscriber_result.json").read_text(encoding="utf-8"))
    _write_json(run_dir / "protocol_negotiation_publisher.json", publisher["protocol_negotiation_publisher"])
    _write_publisher_csv(run_dir / "publisher.csv", run_dir / "publisher.events.jsonl")
    updates = list(read_events(run_dir / "forward_updates.events.jsonl")) if (run_dir / "forward_updates.events.jsonl").is_file() else []
    with (run_dir / "forward_updates.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["event_type", "observed_ts_ns", "user_id", "track_id", "correlation_id", "details"]); writer.writeheader()
        for event in updates: writer.writerow({k: json.dumps(event.get(k)) if k == "details" else event.get(k) for k in writer.fieldnames})
    rows_summary: list[dict[str, Any]] = []
    planned = list(read_manifest(run_dir / "manifest.jsonl"))
    all_received_payload = 0
    all_groups: list[dict[str, Any]] = []
    for result in subscriber["users"]:
        user_id = result["user_id"]
        _write_json(run_dir / f"protocol_negotiation_subscriber_{user_id}.json", result["protocol_negotiation_subscriber"])
        events_path = run_dir / f"subscriber_{user_id}.events.jsonl"
        events = list(read_events(events_path)) if events_path.is_file() else []
        rows = normalize_deliveries(planned, list(read_events(run_dir / "publisher.events.jsonl")) + events, user_id)
        write_object_csv(run_dir / f"subscriber_{user_id}.csv", rows)
        demand = result.get("demand_event_ts_ns")
        demanded = next((u.demand_forward_tracks for u in settings.users if u.user_id == user_id), ())
        first = _first_after(events, set(demanded), demand if isinstance(demand, int) else None)
        expected_forward = 0 if settings.mode == "forward_f0" else 1
        groups = p4_group_rows(planned, events, user_id, expected_forward)
        all_groups.extend(groups)
        first_group = min((item["completion_ts_ns"] for item in groups if item["track_id"] in set(demanded)
                           and item["complete"] and isinstance(item["completion_ts_ns"], int)
                           and isinstance(demand, int) and item["completion_ts_ns"] >= demand), default=None)
        received_payload = sum(e.get("payload_bytes", 0) for e in events if e.get("event_type") == "completed" and isinstance(e.get("payload_bytes"), int))
        all_received_payload += received_payload
        sent = [e for e in updates if e.get("user_id") == user_id and e.get("event_type") == "request_update_sent"]
        ack = [e for e in updates if e.get("user_id") == user_id and e.get("event_type") == "request_ok"]
        sent_ts = min((e.get("observed_ts_ns") for e in sent if isinstance(e.get("observed_ts_ns"), int)), default=None)
        ack_ts = min((e.get("observed_ts_ns") for e in ack if isinstance(e.get("observed_ts_ns"), int)), default=None)
        after_update = [e for e in events if e.get("event_type") == "completed" and isinstance(e.get("observed_ts_ns"), int) and isinstance(sent_ts, int) and e["observed_ts_ns"] >= sent_ts]
        after_ack = [e for e in events if e.get("event_type") == "completed" and isinstance(e.get("observed_ts_ns"), int) and isinstance(ack_ts, int) and e["observed_ts_ns"] >= ack_ts]
        rows_summary.append({"mode": settings.mode, "users": len(settings.users), "overlap": settings.overlap_label, "user_id": user_id,
            "t_first_object_ns": first - demand if first is not None and isinstance(demand, int) else None,
            "t_first_group_ns": first_group - demand if first_group is not None and isinstance(demand, int) else None,
            "viewport_activation_latency_ns": None if len(settings.users) == 1 else first_group - demand if first_group is not None and isinstance(demand, int) else None,
            "request_ok_latency_ns": ack_ts - sent_ts if isinstance(ack_ts, int) and isinstance(sent_ts, int) else None,
            "request_ok_to_first_object_ns": first - ack_ts if first is not None and isinstance(ack_ts, int) else None,
            "request_ok_to_first_group_ns": first_group - ack_ts if first_group is not None and isinstance(ack_ts, int) else None,
            "objects_after_update": len(after_update), "objects_after_request_ok": len(after_ack),
            "bytes_after_update": sum(e.get("payload_bytes", 0) for e in after_update if isinstance(e.get("payload_bytes"), int)),
            "bytes_after_request_ok": sum(e.get("payload_bytes", 0) for e in after_ack if isinstance(e.get("payload_bytes"), int)),
            # A deactivation with no in-flight delivery has no drain time.
            # Do not use the final object from before the update: that would
            # manufacture a negative duration.
            "t_last_object_after_deactivation_ns": max(e["observed_ts_ns"] for e in after_update) - sent_ts if settings.mode == "forward_1_to_0" and after_update and isinstance(sent_ts, int) else None,
            "t_drain_after_ack_ns": max(e["observed_ts_ns"] for e in after_ack) - ack_ts if settings.mode == "forward_1_to_0" and after_ack and isinstance(ack_ts, int) else None,
            "received_payload_bytes": received_payload, "demand_event_ts_ns": demand})
    with (run_dir / "groups.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = ["run_id", "user_id", "track_id", "group_id", "scheduled_publish_ts_ns", "first_object_receive_ts_ns", "last_object_receive_ts_ns", "expected_object_count", "received_object_count", "complete", "completion_ts_ns", "completion_latency_ms", "expected_forward"]
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(all_groups)
    published_payload = sum(e.get("payload_bytes", 0) for e in read_events(run_dir / "publisher.events.jsonl") if e.get("event_type") == "published" and isinstance(e.get("payload_bytes"), int))
    demand_times = [row["demand_event_ts_ns"] for row in rows_summary if isinstance(row["demand_event_ts_ns"], int)]
    demand_tracks = {track for user in settings.users for track in user.demand_forward_tracks}
    first_demand = min(demand_times) if demand_times else None
    unique_required = sum(item.payload_bytes for item in planned if item.track_id in demand_tracks and (first_demand is None or item.scheduled_publish_ts_ns >= first_demand))
    pre_demand_payload = sum(e.get("payload_bytes", 0) for e in read_events(run_dir / "publisher.events.jsonl") if e.get("event_type") == "published" and isinstance(e.get("payload_bytes"), int) and isinstance(first_demand, int) and isinstance(e.get("observed_ts_ns"), int) and e["observed_ts_ns"] < first_demand)
    upstream_before, upstream_after = _counter(run_dir / "network_publisher_before.json", "tx"), _counter(run_dir / "network_publisher_after.json", "tx")
    downstream_before, downstream_after = _counter(run_dir / "network_relay_sub_before.json", "tx"), _counter(run_dir / "network_relay_sub_after.json", "tx")
    upstream_wire = upstream_after - upstream_before if upstream_before is not None and upstream_after is not None else None
    downstream_wire = downstream_after - downstream_before if downstream_before is not None and downstream_after is not None else None
    with (run_dir / "network_counters.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["boundary", "before_bytes", "after_bytes", "delta_bytes", "source"]); writer.writeheader()
        writer.writerow({"boundary": "publisher_to_relay_tx", "before_bytes": upstream_before, "after_bytes": upstream_after, "delta_bytes": upstream_wire, "source": "ip -j -s link"})
        writer.writerow({"boundary": "relay_to_subscribers_tx", "before_bytes": downstream_before, "after_bytes": downstream_after, "delta_bytes": downstream_wire, "source": "ip -j -s link"})
    for row in rows_summary:
        row["publisher_to_relay_payload_bytes"] = published_payload
        row["relay_to_users_payload_bytes"] = all_received_payload
        row["publisher_to_relay_wire_bytes"] = upstream_wire
        row["relay_to_users_wire_bytes"] = downstream_wire
        row["pre_demand_upstream_payload_bytes"] = pre_demand_payload if first_demand is not None else None
        row["unique_required_media_bytes"] = unique_required or None
        row["upstream_amplification"] = upstream_wire / unique_required if upstream_wire is not None and unique_required else None
    with (run_dir / "summary_rows.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows_summary[0]) if rows_summary else ["mode"]); writer.writeheader(); writer.writerows(rows_summary)
    # The root-run script extracts a byte-offset-bounded relay excerpt. Never
    # copy the process-global relay log here, or unrelated prior runs leak in.
    relay_path = run_dir / "relay.log"
    relay_log_saved = relay_path.is_file()
    phases = list(read_events(run_dir / "run_phases.events.jsonl")) if (run_dir / "run_phases.events.jsonl").is_file() else []
    teardown_started = any(e.get("details", {}).get("phase") == "ENDPOINT_TEARDOWN_START" for e in phases)
    relay_errors = classify_relay_errors(relay_path.read_text(encoding="utf-8", errors="replace") if relay_log_saved else "", teardown_started=teardown_started)
    ok_updates = not any(e.get("event_type") == "request_error" for e in updates)
    protocols_ok = publisher["protocol_negotiation_publisher"]["success"] and all(item["protocol_negotiation_subscriber"]["success"] for item in subscriber["users"])
    payload_integrity = all(not any(item.get(key, 0) for key in ("duplicate_objects", "malformed_objects", "unexpected_objects")) for item in subscriber["users"])
    monitor_ok = _monitor_pass(run_dir)
    if settings.mode in {"forward_f1", "forward_f0"}:
        transitions_ok = True
    elif settings.mode == "reactive":
        subscribe_ok = {(e.get("user_id"), e.get("track_id")) for e in updates if e.get("event_type") == "subscribe_ok"}
        transitions_ok = all((user.user_id, track) in subscribe_ok for user in settings.users for track in user.demand_forward_tracks)
    else:
        requested_tracks = {
            (user.user_id, track)
            for user in settings.users
            for track in (user.demand_forward_tracks if settings.mode != "forward_1_to_0" else user.deactivate_tracks)
        }
        request_ok = {(e.get("user_id"), e.get("track_id")) for e in updates if e.get("event_type") == "request_ok"}
        transitions_ok = requested_tracks <= request_ok
    behavior_ok = (all(item.get("received_objects", 0) > 0 for item in subscriber["users"]) if settings.mode == "forward_f1" else
                   all(item.get("received_objects", 0) == 0 for item in subscriber["users"]) if settings.mode == "forward_f0" else
                   all(row.get("t_first_object_ns") is not None for row in rows_summary) if settings.mode in {"forward_0_to_1", "reactive", "fanout"} else
                   all(row.get("objects_after_request_ok", 0) >= 0 for row in rows_summary) if settings.mode == "forward_1_to_0" else True)
    validity = {"protocol": "pass" if protocols_ok else "fail", "payload_integrity": "pass" if payload_integrity else "fail",
        "process_liveness": "pass" if monitor_ok else "fail", "relay_resource": "pass" if not relay_errors["resource_errors"] else "fail",
        "active_window_errors": "pass" if not relay_errors["active_window_errors"] else "fail", "monitoring": "pass" if monitor_ok else "fail",
        "relay_log_scoped": "pass" if relay_log_saved else "fail", "transition": "pass" if transitions_ok and behavior_ok else "fail",
        "teardown_warnings": len(relay_errors["teardown_warnings"])}
    _write_json(run_dir / "provenance.json", capture_provenance(Path.cwd(), run_dir / "config.yaml", Path("configs/capabilities.example.json"), config["protocol"], config["relay"], "draft18_native"))
    valid = bool(protocols_ok and payload_integrity and monitor_ok and relay_log_saved and ok_updates and transitions_ok and behavior_ok
                 and not relay_errors["resource_errors"] and not relay_errors["active_window_errors"] and (run_dir / "provenance.json").is_file())
    summary = {"run_id": plan["run_id"], "test_id": "P4", "mode": settings.mode,
        "status": "COMPLETED" if valid else "COMPLETED_INVALID", "valid_for_protocol_claim": valid,
        "p4c_prewarm": "BLOCKED_BY_CURRENT_RELAY", "relay_log_saved": relay_log_saved, "validity": validity,
        "relay_errors": relay_errors, "phase_event_count": len(phases),
        "publisher_to_relay_payload_bytes": published_payload, "relay_to_users_payload_bytes": all_received_payload,
        "publisher_to_relay_wire_bytes": upstream_wire, "relay_to_users_wire_bytes": downstream_wire,
        "unique_required_media_bytes": unique_required or None,
        "rows": rows_summary, "adapter_result": {"publisher": publisher, "subscriber": subscriber},
        "evaluated_protocol": "draft-ietf-moq-transport-18"}
    _write_json(run_dir / "summary.json", _json_ready(summary))
    return run_dir, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare/finalize a native P4 run.")
    group = parser.add_mutually_exclusive_group(required=True); group.add_argument("--prepare", action="store_true"); group.add_argument("--finalize", type=Path)
    parser.add_argument("--config", type=Path); parser.add_argument("--results", type=Path, default=Path("results"))
    args = parser.parse_args(argv)
    try:
        run_dir, summary = prepare(args.config, args.results) if args.prepare and args.config else finalize(args.finalize) if args.finalize else (_ for _ in ()).throw(ConfigurationError("--prepare requires --config"))
    except (ConfigurationError, P1AdapterError) as exc:
        parser.error(str(exc))
    print(json.dumps(_json_ready({"run_dir": str(run_dir), **summary}), sort_keys=True))
    return 0 if args.prepare or summary["valid_for_protocol_claim"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
