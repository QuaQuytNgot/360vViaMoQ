"""Execute one strict, native draft-18 P1 smoke or measurement run."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .capabilities import capability_snapshot, gate_test
from .config import ConfigurationError, dump_yaml, load_yaml, nested_get, validate_for_run
from .events import read_events
from .experiment import _prepare_result_tables, _synthetic_workload, _write_json
from .manifest import write_manifest
from .metrics import group_completion_times, normalize_deliveries, p1_summary, write_object_csv
from .p1_adapter import Moqt18P1Adapter, P1AdapterError, P1AdapterSettings
from .pacing import LivePacer
from .provenance import capture_provenance
from .workload import plan_synthetic_objects


def _run_id() -> str:
    return "run_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]


def _require(mapping: Mapping[str, Any], dotted: str) -> Any:
    value = nested_get(mapping, dotted)
    if value is None or value == "":
        raise ConfigurationError(f"Missing {dotted}")
    return value


def _append_summary(path: Path, row: Mapping[str, Any]) -> None:
    fields = [
        "run_id", "track_count", "total_offered_bitrate_mbps", "network_capacity_mbps", "repetition",
        "mean_completion_latency_ms", "median_completion_latency_ms", "p95_completion_latency_ms",
        "mean_skew_ms", "median_skew_ms", "p95_skew_ms", "p99_skew_ms", "weakest_track_goodput_bps",
        "aggregate_goodput_bps", "group_completion_ratio", "deadline_miss_ratio", "valid_for_protocol_claim",
    ]
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if not exists:
            writer.writeheader()
        writer.writerow({key: row.get(key) for key in fields})


def _write_publisher_csv(path: Path, event_path: Path) -> None:
    fields = ["run_id", "track_id", "tile_id", "group_id", "object_id", "scheduled_publish_ts_ns", "actual_publish_ts_ns", "pacer_lateness_ns", "payload_bytes"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for event in read_events(event_path):
            if event.get("event_type") != "published":
                continue
            details = event.get("details") if isinstance(event.get("details"), Mapping) else {}
            writer.writerow({
                "run_id": event.get("run_id"), "track_id": event.get("track_id"), "tile_id": event.get("tile_id"),
                "group_id": event.get("group_id"), "object_id": event.get("object_id"),
                "scheduled_publish_ts_ns": details.get("scheduled_publish_ts_ns"),
                "actual_publish_ts_ns": event.get("observed_ts_ns"), "pacer_lateness_ns": details.get("pacer_lateness_ns"),
                "payload_bytes": event.get("payload_bytes"),
            })


def _ms(value: object) -> float | None:
    return float(value) / 1_000_000 if isinstance(value, (int, float)) else None


def _json_ready(value: Any) -> Any:
    """Make tuple-keyed metric maps explicit and JSON-safe for evidence."""
    if isinstance(value, Mapping):
        return {"/".join(str(part) for part in key) if isinstance(key, tuple) else str(key): _json_ready(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    return value


def run(config_path: Path, capability_path: Path, results_root: Path) -> tuple[Path, dict[str, Any]]:
    config = load_yaml(config_path)
    errors = validate_for_run(config)
    if errors:
        raise ConfigurationError("\n".join(errors))
    if nested_get(config, "runtime.mode") != "live" or nested_get(config, "experiment.test_id") != "P1":
        raise ConfigurationError("p1_runner requires runtime.mode=live and experiment.test_id=P1")
    if nested_get(config, "protocol.backend") != "moqt18" or nested_get(config, "protocol.draft") != 18 or nested_get(config, "protocol.transport") != "raw_quic":
        raise ConfigurationError("p1_runner requires strict moqt18/raw_quic/draft 18")
    if nested_get(config, "workload.media_mode") != "synthetic":
        raise ConfigurationError("P1 native runner accepts deterministic synthetic workload only")
    operation = _require(config, "runtime.operation")
    if operation not in {"p1_smoke", "p1_measurement"}:
        raise ConfigurationError("runtime.operation must be p1_smoke or p1_measurement")
    start_delay_ms = _require(config, "runtime.start_delay_ms")
    if not isinstance(start_delay_ms, int) or start_delay_ms < 0:
        raise ConfigurationError("runtime.start_delay_ms must be a non-negative integer")
    # A measurement needs independently saved smoke evidence.  A smoke is the
    # evidence-producing operation and may proceed without changing the ledger.
    if operation == "p1_measurement":
        gate = gate_test("P1", "live", capability_path, probe_succeeded=True)
        if not gate.runnable:
            raise ConfigurationError("P1 measurement is capability-gated: " + "; ".join(gate.reasons))

    run_id = _run_id()
    run_dir = results_root / "P1" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    _prepare_result_tables(run_dir)
    dump_yaml(run_dir / "config.yaml", config)
    _write_json(run_dir / "capability_snapshot.json", capability_snapshot(capability_path, "moqt18"))
    workload = _synthetic_workload(config)
    anchor = LivePacer().anchor_ts_ns + start_delay_ms * 1_000_000
    planned = list(plan_synthetic_objects(workload, run_id, anchor))
    write_manifest(run_dir / "manifest.jsonl", planned)
    settings = P1AdapterSettings.from_config(config, namespace=f"moq-360-p1/{run_id}")
    adapter = Moqt18P1Adapter(settings, run_dir / "publisher.events.jsonl", run_dir / "subscriber.events.jsonl")
    adapter_result = asyncio.run(adapter.run(planned))
    _write_json(run_dir / "protocol_negotiation_publisher.json", adapter_result["protocol_negotiation_publisher"])
    _write_json(run_dir / "protocol_negotiation_subscriber.json", adapter_result["protocol_negotiation_subscriber"])
    # Compatibility with existing provenance tooling while retaining both
    # independent session observations as the authoritative records.
    _write_json(run_dir / "protocol_negotiation.json", adapter_result["protocol_negotiation_publisher"])
    _write_publisher_csv(run_dir / "publisher.csv", run_dir / "publisher.events.jsonl")
    rows = normalize_deliveries(planned, list(read_events(run_dir / "publisher.events.jsonl")) + list(read_events(run_dir / "subscriber.events.jsonl")), settings.user_id)
    write_object_csv(run_dir / "subscriber.csv", rows)
    completed_groups = group_completion_times(rows)
    groups_path = run_dir / "groups.csv"
    with groups_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["run_id", "group_id", "track_id", "complete_receive_ts_ns", "completed"])
        writer.writeheader()
        for item in planned:
            group_ts = completed_groups.get((settings.user_id, item.tile_id, item.group_id))
            writer.writerow({"run_id": run_id, "group_id": item.group_id, "track_id": item.track_id,
                             "complete_receive_ts_ns": group_ts, "completed": group_ts is not None})
    deadline_ms = nested_get(config, "playback.deadline_ms")
    deadline_ns = deadline_ms * 1_000_000 if isinstance(deadline_ms, int) else None
    p1 = p1_summary(rows, workload.tile_ids, int(adapter_result["started_ts_ns"]), int(adapter_result["ended_ts_ns"]), deadline_ns)
    relay_log_path = nested_get(config, "runtime.relay_log_path")
    relay_log_saved = False
    if isinstance(relay_log_path, str) and relay_log_path:
        source = Path(relay_log_path)
        if source.is_file():
            shutil.copy2(source, run_dir / "relay.log")
            relay_log_saved = True
    publisher_ok = bool(adapter_result["protocol_negotiation_publisher"]["success"])
    subscriber_ok = bool(adapter_result["protocol_negotiation_subscriber"]["success"])
    valid = publisher_ok and subscriber_ok and bool(adapter_result["integrity_passed"]) and relay_log_saved
    summary: dict[str, Any] = {
        "run_id": run_id, "test_id": "P1", "operation": operation, "status": "COMPLETED" if valid else "COMPLETED_INVALID",
        "evaluated_protocol": "draft-ietf-moq-transport-18", "valid_for_protocol_claim": valid,
        "relay_log_saved": relay_log_saved, "adapter": "Moqt18P1Adapter", "adapter_result": adapter_result, "p1": p1,
    }
    _write_json(run_dir / "summary.json", _json_ready(summary))
    provenance = capture_provenance(Path.cwd(), config_path, capability_path, config["protocol"], config["relay"], "draft18_native" if valid else "no_protocol_execution")
    _write_json(run_dir / "provenance.json", provenance)
    latency = p1["completion_latency_ns"]
    skew = p1["cross_track_completion_skew_ns"]
    assert isinstance(latency, Mapping) and isinstance(skew, Mapping)
    offered_bps = sum(workload.per_track_bitrate_bps)
    network_capacity = nested_get(config, "network.bandwidth_mbps")
    _append_summary(results_root / "P1" / "p1_summary.csv", {
        "run_id": run_id, "track_count": len(workload.track_ids), "total_offered_bitrate_mbps": offered_bps / 1_000_000,
        "network_capacity_mbps": network_capacity, "repetition": nested_get(config, "experiment.repetition") or 1,
        "mean_completion_latency_ms": _ms(latency.get("mean")), "median_completion_latency_ms": _ms(latency.get("median")),
        "p95_completion_latency_ms": _ms(latency.get("p95")), "mean_skew_ms": _ms(skew.get("mean")),
        "median_skew_ms": _ms(skew.get("median")), "p95_skew_ms": _ms(skew.get("p95")), "p99_skew_ms": _ms(skew.get("p99")),
        "weakest_track_goodput_bps": p1.get("weakest_track_goodput_bps"), "aggregate_goodput_bps": p1.get("aggregate_goodput_bps"),
        "group_completion_ratio": p1.get("group_completion_ratio"), "deadline_miss_ratio": p1.get("deadline_miss_ratio"),
        "valid_for_protocol_claim": valid,
    })
    return run_dir, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one strict raw-QUIC draft-18 synthetic P1 operation.")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--capabilities", default=Path("configs/capabilities.example.json"), type=Path)
    parser.add_argument("--results", default=Path("results"), type=Path)
    args = parser.parse_args(argv)
    try:
        run_dir, summary = run(args.config, args.capabilities, args.results)
    except (ConfigurationError, P1AdapterError) as exc:
        parser.error(str(exc))
    print(json.dumps(_json_ready({"run_dir": str(run_dir), **summary}), sort_keys=True))
    return 0 if summary["valid_for_protocol_claim"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
