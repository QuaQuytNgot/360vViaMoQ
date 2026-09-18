"""Run one native P2 static-priority control or single-switch experiment."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import statistics
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .config import ConfigurationError, dump_yaml, load_yaml, nested_get, validate_for_run
from .events import read_events
from .experiment import _prepare_result_tables, _synthetic_workload, _write_json
from .manifest import read_manifest, write_manifest
from .metrics import group_completion_times, normalize_deliveries, p1_summary, write_object_csv
from .p1_runner import _json_ready, _write_publisher_csv
from .p1_adapter import P1AdapterError
from .p2_adapter import P2AdapterSettings
from .pacing import LivePacer
from .provenance import capture_provenance
from .workload import plan_synthetic_objects


def _run_id() -> str:
    return "run_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]


def _p2_metrics(subscriber_events: list[dict[str, Any]], update_events: list[dict[str, Any]], initial: Mapping[str, int], updated: Mapping[str, int], window_ms: int, threshold: float) -> dict[str, Any]:
    sent = [event for event in update_events if event.get("event_type") == "request_update_sent"]
    responses = [event for event in update_events if event.get("event_type") in {"request_ok", "request_error"}]
    controls = [event.get("details", {}).get("control_response_latency_ns") for event in responses if event.get("event_type") == "request_ok"]
    controls = [value for value in controls if isinstance(value, int)]
    if not sent or not updated:
        return {"request_update_count": 0, "request_ok_count": 0, "request_error_count": 0,
                "control_response_latency_ns": {}, "receiver_observed_first_effect_latency_ns": None,
                "receiver_observed_stable_reaction_latency_ns": None, "stale_bytes_after_switch": 0,
                "stale_objects_after_switch": 0, "reaction_observation": "not_applicable_static_control"}
    switch_ts = max(event["observed_ts_ns"] for event in sent if isinstance(event.get("observed_ts_ns"), int))
    high_tracks = {track for track, priority in updated.items() if priority < initial.get(track, 256)}
    old_high_tracks = {track for track, priority in updated.items() if initial.get(track, 256) < priority}
    completed = [event for event in subscriber_events if event.get("event_type") == "completed" and isinstance(event.get("observed_ts_ns"), int)]
    first = min((event["observed_ts_ns"] for event in completed if event.get("track_id") in high_tracks and event["observed_ts_ns"] >= switch_ts), default=None)
    win_ns = window_ms * 1_000_000
    buckets: dict[int, dict[str, int]] = {}
    for event in completed:
        ts = event["observed_ts_ns"]
        if ts < switch_ts: continue
        bucket = (ts - switch_ts) // win_ns
        record = buckets.setdefault(bucket, {"high": 0, "total": 0})
        payload = event.get("payload_bytes") if isinstance(event.get("payload_bytes"), int) else 0
        record["total"] += payload
        if event.get("track_id") in high_tracks: record["high"] += payload
    stable = None
    for bucket in sorted(buckets):
        share = buckets[bucket]["high"] / buckets[bucket]["total"] if buckets[bucket]["total"] else 0
        following = buckets.get(bucket + 1)
        following_share = following["high"] / following["total"] if following and following["total"] else 0
        if share >= threshold and following_share >= threshold:
            # A bucket that begins at the switch can contain an immediate
            # post-switch Object.  Report its *end* as a conservative
            # receiver-observed stable-reaction bound, never a misleading
            # zero-latency reaction.
            stable = switch_ts + (bucket + 1) * win_ns
            break
    stale = [event for event in completed if event.get("track_id") in old_high_tracks and event["observed_ts_ns"] >= switch_ts]
    return {"request_update_count": len(sent), "request_ok_count": sum(event.get("event_type") == "request_ok" for event in responses),
            "request_error_count": sum(event.get("event_type") == "request_error" for event in responses),
            "control_response_latency_ns": {"count": len(controls), "median": statistics.median(controls) if controls else None,
                                            "max": max(controls) if controls else None},
            "receiver_observed_first_effect_latency_ns": first - switch_ts if first is not None else None,
            "receiver_observed_stable_reaction_latency_ns": stable - switch_ts if stable is not None else None,
            "stale_bytes_after_switch": sum(event.get("payload_bytes", 0) for event in stale),
            "stale_objects_after_switch": len(stale), "reaction_observation": "receiver_observed_proxy",
            "rolling_window_ms": window_ms, "dominance_threshold": threshold,
            "new_high_priority_tracks": sorted(high_tracks), "old_high_priority_tracks": sorted(old_high_tracks)}


def prepare(config_path: Path, results_root: Path) -> tuple[Path, dict[str, Any]]:
    config = load_yaml(config_path)
    errors = validate_for_run(config)
    if errors: raise ConfigurationError("\n".join(errors))
    if nested_get(config, "runtime.mode") != "live" or nested_get(config, "experiment.test_id") != "P2":
        raise ConfigurationError("p2_runner requires live P2 configuration")
    if (nested_get(config, "protocol.backend"), nested_get(config, "protocol.draft"), nested_get(config, "protocol.transport")) != ("moqt18", 18, "raw_quic"):
        raise ConfigurationError("p2_runner requires strict moqt18/raw_quic/draft 18")
    p2 = config.get("p2")
    if not isinstance(p2, Mapping): raise ConfigurationError("P2 requires p2 mapping")
    interval = p2.get("object_interval_ms")
    if not isinstance(interval, int) or interval <= 0: raise ConfigurationError("p2.object_interval_ms must be positive")
    window, threshold = p2.get("rolling_window_ms"), p2.get("dominance_threshold")
    if not isinstance(window, int) or window <= 0 or not isinstance(threshold, (int, float)) or not 0 < threshold <= 1:
        raise ConfigurationError("P2 rolling_window_ms and dominance_threshold must be configured")
    workload = _synthetic_workload(config)
    if interval * workload.objects_per_group > workload.group_duration_ns // 1_000_000:
        raise ConfigurationError("objects_per_group * object_interval_ms exceeds group_duration_ms")
    operation = nested_get(config, "runtime.operation")
    if operation not in {"p2_static_control", "p2_single_switch"}:
        raise ConfigurationError("runtime.operation must be p2_static_control or p2_single_switch")
    start_delay_ms = nested_get(config, "runtime.start_delay_ms")
    if not isinstance(start_delay_ms, int) or start_delay_ms < 0:
        raise ConfigurationError("runtime.start_delay_ms must be a non-negative integer")
    if operation == "p2_static_control" and (p2.get("switch_at_ms") is not None or p2.get("updated_priorities")):
        raise ConfigurationError("p2_static_control must not configure a dynamic update")
    if operation == "p2_single_switch" and p2.get("switch_at_ms") is None:
        raise ConfigurationError("p2_single_switch requires p2.switch_at_ms")
    run_id = _run_id()
    run_dir = results_root / "P2" / run_id
    run_dir.mkdir(parents=True, exist_ok=False); _prepare_result_tables(run_dir); dump_yaml(run_dir / "config.yaml", config)
    anchor = LivePacer().anchor_ts_ns + start_delay_ms * 1_000_000
    planned = [replace(item, logical_media_time_ns=item.logical_media_time_ns + item.object_id * interval * 1_000_000,
                       scheduled_publish_ts_ns=item.scheduled_publish_ts_ns + item.object_id * interval * 1_000_000)
               for item in plan_synthetic_objects(workload, run_id, anchor)]
    write_manifest(run_dir / "manifest.jsonl", planned)
    P2AdapterSettings.from_config(config, namespace=f"moq-360-p2/{run_id}")
    _write_json(run_dir / "endpoint_plan.json", {"run_id": run_id, "source_anchor_ts_ns": anchor, "operation": operation,
                                                   "publisher_namespace": "moq-p2-pub", "subscriber_namespace": "moq-p2-sub"})
    return run_dir, {"run_id": run_id, "source_anchor_ts_ns": anchor, "operation": operation,
                     "expected_objects": len(planned), "track_count": len(workload.track_ids)}


def finalize(run_dir: Path) -> tuple[Path, dict[str, Any]]:
    """Join independently produced endpoint evidence without re-running traffic."""
    run_dir = run_dir.resolve()
    config = load_yaml(run_dir / "config.yaml")
    p2 = config.get("p2")
    if not isinstance(p2, Mapping):
        raise ConfigurationError("saved P2 config has no p2 mapping")
    settings = P2AdapterSettings.from_config(config, namespace=f"moq-360-p2/{json.loads((run_dir / 'endpoint_plan.json').read_text(encoding='utf-8'))['run_id']}")
    window, threshold = p2.get("rolling_window_ms"), p2.get("dominance_threshold")
    if not isinstance(window, int) or window <= 0 or not isinstance(threshold, (int, float)) or not 0 < threshold <= 1:
        raise ConfigurationError("saved P2 rolling_window_ms and dominance_threshold are invalid")
    failures = [path for path in (run_dir / "publisher_failure.json", run_dir / "subscriber_failure.json") if path.exists()]
    results = []
    for role in ("publisher", "subscriber"):
        path = run_dir / f"{role}_result.json"
        if not path.is_file():
            raise ConfigurationError(f"missing {path.name}; endpoint did not finish" + (f" (failure evidence: {failures})" if failures else ""))
        results.append(json.loads(path.read_text(encoding="utf-8")))
    publisher_result, subscriber_result = results
    _write_json(run_dir / "protocol_negotiation_publisher.json", publisher_result["protocol_negotiation_publisher"])
    _write_json(run_dir / "protocol_negotiation_subscriber.json", subscriber_result["protocol_negotiation_subscriber"])
    _write_publisher_csv(run_dir / "publisher.csv", run_dir / "publisher.events.jsonl")
    subscriber_events = list(read_events(run_dir / "subscriber.events.jsonl"))
    updates_path = run_dir / "request_updates.events.jsonl"
    # Static-priority controls intentionally issue no REQUEST_UPDATE frames.
    update_events = list(read_events(updates_path)) if updates_path.is_file() else []
    planned = list(read_manifest(run_dir / "manifest.jsonl"))
    rows = normalize_deliveries(planned, list(read_events(run_dir / "publisher.events.jsonl")) + subscriber_events, settings.user_id)
    write_object_csv(run_dir / "subscriber.csv", rows)
    with (run_dir / "request_updates.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["event_type", "observed_ts_ns", "track_id", "correlation_id", "details"]); writer.writeheader()
        for event in update_events: writer.writerow({key: json.dumps(event.get(key)) if key == "details" else event.get(key) for key in writer.fieldnames})
    with (run_dir / "groups.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["group_id", "track_id", "complete_receive_ts_ns", "completed"]); writer.writeheader()
        groups = group_completion_times(rows)
        for item in planned: writer.writerow({"group_id": item.group_id, "track_id": item.track_id, "complete_receive_ts_ns": groups.get((settings.user_id, item.tile_id, item.group_id)), "completed": (settings.user_id, item.tile_id, item.group_id) in groups})
    workload = _synthetic_workload(config)
    deadline_ms = nested_get(config, "playback.deadline_ms")
    deadline_ns = deadline_ms * 1_000_000 if isinstance(deadline_ms, int) else None
    p1 = p1_summary(rows, workload.tile_ids, int(min(publisher_result["started_ts_ns"], subscriber_result["started_ts_ns"])),
                    int(max(publisher_result["ended_ts_ns"], subscriber_result["ended_ts_ns"])), deadline_ns)
    metrics = _p2_metrics(subscriber_events, update_events, settings.initial_priorities, settings.updated_priorities, window, float(threshold))
    relay_log_saved = False
    log = nested_get(config, "runtime.relay_log_path")
    if isinstance(log, str) and Path(log).is_file(): shutil.copy2(log, run_dir / "relay.log"); relay_log_saved = True
    endpoint_plan = json.loads((run_dir / "endpoint_plan.json").read_text(encoding="utf-8"))
    dynamic_ok = not settings.updated_priorities or (metrics["request_update_count"] == len(settings.updated_priorities)
                  and metrics["request_ok_count"] == len(settings.updated_priorities) and metrics["request_error_count"] == 0)
    valid = bool(subscriber_result["integrity_passed"] and publisher_result["protocol_negotiation_publisher"]["success"]
                 and subscriber_result["protocol_negotiation_subscriber"]["success"] and relay_log_saved and dynamic_ok)
    summary = {"run_id": endpoint_plan["run_id"], "test_id": "P2", "operation": endpoint_plan["operation"],
               "status": "COMPLETED" if valid else "COMPLETED_INVALID", "valid_for_protocol_claim": valid,
               "adapter_result": {"publisher": publisher_result, "subscriber": subscriber_result}, "p1_compatible_metrics": p1, "p2": metrics, "relay_log_saved": relay_log_saved,
               "evaluated_protocol": "draft-ietf-moq-transport-18"}
    _write_json(run_dir / "summary.json", _json_ready(summary))
    _write_json(run_dir / "provenance.json", capture_provenance(Path.cwd(), run_dir / "config.yaml", Path("configs/capabilities.example.json"), config["protocol"], config["relay"], "draft18_native" if valid else "no_protocol_execution"))
    return run_dir, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare or finalize a strict isolated draft-18 P2 run.")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--prepare", action="store_true")
    modes.add_argument("--finalize", type=Path)
    parser.add_argument("--config", type=Path); parser.add_argument("--results", default=Path("results"), type=Path)
    args = parser.parse_args(argv)
    try:
        if args.prepare:
            if args.config is None:
                parser.error("--prepare requires --config")
            run_dir, summary = prepare(args.config, args.results)
        else:
            run_dir, summary = finalize(args.finalize)
    except (ConfigurationError, P1AdapterError) as exc: parser.error(str(exc))
    print(json.dumps(_json_ready({"run_dir": str(run_dir), **summary}), sort_keys=True))
    return 0 if args.prepare or summary["valid_for_protocol_claim"] else 1


if __name__ == "__main__": raise SystemExit(main())
