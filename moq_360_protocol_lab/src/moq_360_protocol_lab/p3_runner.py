"""Prepare/finalize strict native P3A Object Delivery Timeout evidence."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
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
from .p3_adapter import P3AdapterSettings
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
    if nested_get(config, "runtime.mode") != "live" or nested_get(config, "experiment.test_id") != "P3":
        raise ConfigurationError("p3_runner requires a live P3 configuration")
    if (nested_get(config, "protocol.backend"), nested_get(config, "protocol.draft"), nested_get(config, "protocol.transport")) != ("moqt18", 18, "raw_quic"):
        raise ConfigurationError("p3_runner requires strict moqt18/raw_quic/draft 18")
    settings_probe = P3AdapterSettings.from_config(config, namespace="preflight")
    workload = _synthetic_workload(config)
    interval = nested_get(config, "p3.object_interval_ms")
    if not isinstance(interval, int) or interval <= 0:
        raise ConfigurationError("p3.object_interval_ms must be positive")
    if interval * workload.objects_per_group > workload.group_duration_ns // 1_000_000:
        raise ConfigurationError("objects_per_group * p3.object_interval_ms exceeds group_duration_ms")
    start_delay_ms = nested_get(config, "runtime.start_delay_ms")
    if not isinstance(start_delay_ms, int) or start_delay_ms < 0:
        raise ConfigurationError("runtime.start_delay_ms must be non-negative")
    run_id = _run_id()
    run_dir = results_root / "P3" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    _prepare_result_tables(run_dir)
    dump_yaml(run_dir / "config.yaml", config)
    anchor = LivePacer().anchor_ts_ns + start_delay_ms * 1_000_000
    planned = [replace(item, logical_media_time_ns=item.logical_media_time_ns + item.object_id * interval * 1_000_000,
                       scheduled_publish_ts_ns=item.scheduled_publish_ts_ns + item.object_id * interval * 1_000_000)
               for item in plan_synthetic_objects(workload, run_id, anchor)]
    write_manifest(run_dir / "manifest.jsonl", planned)
    _write_json(run_dir / "endpoint_plan.json", {"run_id": run_id, "source_anchor_ts_ns": anchor,
                "publisher_namespace": "moq-p2-pub", "subscriber_namespace": "moq-p2-sub",
                "p3_treatment": settings_probe.treatment})
    return run_dir, {"run_id": run_id, "expected_objects": len(planned), "track_count": len(workload.track_ids), "treatment": settings_probe.treatment}


def finalize(run_dir: Path) -> tuple[Path, dict[str, Any]]:
    run_dir = run_dir.resolve()
    config = load_yaml(run_dir / "config.yaml")
    plan = json.loads((run_dir / "endpoint_plan.json").read_text(encoding="utf-8"))
    settings = P3AdapterSettings.from_config(config, namespace=f"moq-360-p3/{plan['run_id']}")
    results: list[dict[str, Any]] = []
    for role in ("publisher", "subscriber"):
        path = run_dir / f"{role}_result.json"
        if not path.is_file():
            raise ConfigurationError(f"missing {path.name}; endpoint did not finish")
        results.append(json.loads(path.read_text(encoding="utf-8")))
    publisher, subscriber = results
    _write_json(run_dir / "protocol_negotiation_publisher.json", publisher["protocol_negotiation_publisher"])
    _write_json(run_dir / "protocol_negotiation_subscriber.json", subscriber["protocol_negotiation_subscriber"])
    _write_publisher_csv(run_dir / "publisher.csv", run_dir / "publisher.events.jsonl")
    subscriber_events = list(read_events(run_dir / "subscriber.events.jsonl"))
    planned = list(read_manifest(run_dir / "manifest.jsonl"))
    rows = normalize_deliveries(planned, list(read_events(run_dir / "publisher.events.jsonl")) + subscriber_events, settings.user_id)
    write_object_csv(run_dir / "subscriber.csv", rows)
    with (run_dir / "groups.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["group_id", "track_id", "complete_receive_ts_ns", "completed"]); writer.writeheader()
        groups = group_completion_times(rows)
        for item in planned:
            key = (settings.user_id, item.tile_id, item.group_id)
            writer.writerow({"group_id": item.group_id, "track_id": item.track_id, "complete_receive_ts_ns": groups.get(key), "completed": key in groups})
    workload = _synthetic_workload(config)
    deadline_ms = nested_get(config, "playback.deadline_ms")
    p1 = p1_summary(rows, workload.tile_ids, int(min(publisher["started_ts_ns"], subscriber["started_ts_ns"])), int(max(publisher["ended_ts_ns"], subscriber["ended_ts_ns"])), deadline_ms * 1_000_000 if isinstance(deadline_ms, int) else None)
    relay_log_saved = False
    relay_faults: list[str] = []
    log = nested_get(config, "runtime.relay_log_path")
    if isinstance(log, str) and Path(log).is_file():
        shutil.copy2(log, run_dir / "relay.log"); relay_log_saved = True
        relay_text = (run_dir / "relay.log").read_text(encoding="utf-8", errors="replace")
        # These are forwarding-resource failures, not delivery-timeout
        # actions.  They make Object loss attribution ambiguous and must
        # invalidate the run even when a native timeout reset was observed.
        relay_faults = [marker for marker in ("Failed to create uni stream", "beginSubgroup failed") if marker in relay_text]
    reset_count = int(subscriber.get("native_delivery_timeout_reset_count", 0))
    control_clean = settings.treatment != "control_no_timeout" or (
        reset_count == 0 and int(subscriber["missing_objects"]) == 0
    )
    native_evidence = (settings.treatment == "control_no_timeout" and control_clean) or reset_count > 0
    integrity = bool(subscriber["integrity_passed"])
    valid = bool(integrity and publisher["protocol_negotiation_publisher"]["success"] and subscriber["protocol_negotiation_subscriber"]["success"] and relay_log_saved and native_evidence and not relay_faults)
    summary = {"run_id": plan["run_id"], "test_id": "P3", "operation": "p3_object_delivery_timeout",
               "treatment": settings.treatment, "object_delivery_timeout_ms": settings.object_delivery_timeout_ms,
               "status": "COMPLETED" if valid else "COMPLETED_INVALID", "valid_for_protocol_claim": valid,
               "adapter_result": {"publisher": publisher, "subscriber": subscriber}, "p1_compatible_metrics": p1,
               "p3": {"native_delivery_timeout_reset_count": reset_count, "native_stream_reset_count": subscriber.get("native_stream_reset_count", 0), "native_timeout_evidence": native_evidence, "control_clean": control_clean, "relay_fault_markers": relay_faults},
               "relay_log_saved": relay_log_saved, "evaluated_protocol": "draft-ietf-moq-transport-18"}
    _write_json(run_dir / "summary.json", _json_ready(summary))
    _write_json(run_dir / "provenance.json", capture_provenance(Path.cwd(), run_dir / "config.yaml", Path("configs/capabilities.example.json"), config["protocol"], config["relay"], "draft18_native" if valid else "no_protocol_execution"))
    return run_dir, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare or finalize a strict P3A draft-18 native run.")
    mode = parser.add_mutually_exclusive_group(required=True); mode.add_argument("--prepare", action="store_true"); mode.add_argument("--finalize", type=Path)
    parser.add_argument("--config", type=Path); parser.add_argument("--results", default=Path("results"), type=Path)
    args = parser.parse_args(argv)
    try:
        run_dir, summary = prepare(args.config, args.results) if args.prepare and args.config else finalize(args.finalize) if args.finalize else (_ for _ in ()).throw(ConfigurationError("--prepare requires --config"))
    except ConfigurationError as exc:
        parser.error(str(exc))
    print(json.dumps(_json_ready({"run_dir": str(run_dir), **summary}), sort_keys=True))
    return 0 if args.prepare or summary["valid_for_protocol_claim"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
