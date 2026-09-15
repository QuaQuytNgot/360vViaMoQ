"""Experiment entry point with strict protocol selection and evidence gates."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .backends import BackendError, ProtocolProbe, backend_for
from .capabilities import capability_snapshot, gate_test
from .config import ConfigurationError, dump_yaml, load_yaml, nested_get, validate_for_run
from .events import EventRecorder
from .manifest import read_manifest, reanchor_manifest, write_manifest
from .metrics import OBJECT_COLUMNS
from .pacing import LivePacer
from .provenance import capture_provenance
from .publisher import LivePublisher
from .workload import SyntheticWorkload, plan_synthetic_objects


def _run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"run_{timestamp}_{uuid.uuid4().hex[:8]}"


def _path(root: Path, config: Mapping[str, Any], run_id: str) -> Path:
    test_id = nested_get(config, "experiment.test_id")
    if not isinstance(test_id, str) or not test_id:
        raise ConfigurationError("experiment.test_id is required")
    return root / test_id / run_id


def _mapping(config: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = config.get(key)
    if not isinstance(value, Mapping):
        raise ConfigurationError(f"{key} must be a mapping")
    return value


def _synthetic_workload(config: Mapping[str, Any]) -> SyntheticWorkload:
    workload = config.get("workload")
    if not isinstance(workload, Mapping):
        raise ConfigurationError("workload must be a mapping")
    try:
        workload_model = SyntheticWorkload(
            track_ids=tuple(_string_list(workload["track_ids"], "workload.track_ids")),
            tile_ids=tuple(_string_list(workload["tile_ids"], "workload.tile_ids")),
            per_track_bitrate_bps=tuple(_integer_list(workload["per_track_bitrate_bps"], "workload.per_track_bitrate_bps")),
            group_duration_ns=_positive_int(workload["group_duration_ms"], "workload.group_duration_ms") * 1_000_000,
            group_count=_positive_int(workload["group_count"], "workload.group_count"),
            objects_per_group=_positive_int(workload["objects_per_group"], "workload.objects_per_group"),
            seed=_nonempty_string(workload["seed"], "workload.seed"),
        )
        declared_count = _positive_int(workload["track_count"], "workload.track_count")
        if declared_count != len(workload_model.track_ids):
            raise ConfigurationError("workload.track_count must equal the number of explicit track_ids")
        return workload_model
    except KeyError as exc:
        raise ConfigurationError(f"Missing workload setting: {exc.args[0]}") from exc


def _string_list(value: object, name: str) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
        raise ConfigurationError(f"{name} must be a non-empty string list")
    return value


def _integer_list(value: object, name: str) -> list[int]:
    if not isinstance(value, list) or not value or not all(isinstance(item, int) and item > 0 for item in value):
        raise ConfigurationError(f"{name} must be a non-empty list of positive integers")
    return value


def _positive_int(value: object, name: str) -> int:
    if not isinstance(value, int) or value <= 0:
        raise ConfigurationError(f"{name} must be a positive integer")
    return value


def _nonempty_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigurationError(f"{name} must be a non-empty string")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_csv_header(path: Path, columns: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerow(columns)


def _prepare_result_tables(run_dir: Path) -> None:
    (run_dir / "logs").mkdir()
    (run_dir / "analysis").mkdir()
    _write_csv_header(run_dir / "publisher.csv", [
        "run_id", "track_id", "tile_id", "group_id", "object_id",
        "scheduled_publish_ts_ns", "actual_publish_ts_ns", "pacer_lateness_ns", "payload_bytes",
    ])
    _write_csv_header(run_dir / "subscriber.csv", OBJECT_COLUMNS)
    _write_csv_header(run_dir / "transport.csv", [
        "run_id", "user_id", "sample_ts_ns", "clock_domain_id", "rtt_us",
        "send_rate_bps", "recv_rate_bps", "bytes_sent", "bytes_received", "bytes_lost",
        "packets_sent", "packets_received", "packets_lost", "provenance",
    ])
    _write_csv_header(run_dir / "control.csv", [
        "run_id", "user_id", "correlation_id", "operation", "generated_ts_ns",
        "sent_ts_ns", "ack_ts_ns", "effective_ts_ns", "control_bytes", "status", "provenance",
    ])


def _not_attempted_probe(protocol: Mapping[str, Any], relay: Mapping[str, Any], reason: str) -> dict[str, Any]:
    draft = protocol.get("draft")
    return ProtocolProbe(
        requested_draft=draft if isinstance(draft, int) else None,
        negotiated_draft=None,
        transport=str(protocol.get("transport") or ""),
        alpn=None,
        relay={key: relay.get(key) for key in ("implementation", "version", "commit", "address", "port", "path")},
        success=False,
        reason=reason,
        backend=str(protocol.get("backend") or ""),
    ).as_dict()


def _claim_scope(mode: str, backend_name: str, protocol_ready: bool) -> str:
    if mode == "synthetic_plan":
        return "harness_validation_not_moq"
    if mode == "event_replay":
        return "existing_observations_only"
    if backend_name == "moq_lite":
        return "moq_lite_only" if protocol_ready else "no_protocol_execution"
    return "draft18_native" if protocol_ready else "no_protocol_execution"


def run(config_path: Path, capability_path: Path, result_root: Path, *, execute_plan: bool = False) -> tuple[Path, dict[str, Any]]:
    config = load_yaml(config_path)
    errors = validate_for_run(config)
    if errors:
        raise ConfigurationError("\n".join(errors))
    protocol = _mapping(config, "protocol")
    relay = _mapping(config, "relay") if isinstance(config.get("relay"), Mapping) else {}
    backend_name = protocol["backend"]
    assert isinstance(backend_name, str)
    backend = backend_for(backend_name)
    mode = nested_get(config, "runtime.mode")
    test_id = nested_get(config, "experiment.test_id")
    assert isinstance(mode, str) and isinstance(test_id, str)

    run_id = _run_id()
    run_dir = _path(result_root, config, run_id)
    run_dir.mkdir(parents=True, exist_ok=False)
    _prepare_result_tables(run_dir)
    dump_yaml(run_dir / "config.effective.yaml", config)
    _write_json(run_dir / "capability_snapshot.json", capability_snapshot(capability_path, backend_name))
    _write_json(run_dir / "data_quality.json", {
        "clock_domains_compatible": None,
        "required_observations_complete": None,
        "valid_for_protocol_claim": False,
        "notes": ["Unset values are unavailable, not zero.", "No fallback protocol is accepted."],
    })
    _write_json(run_dir / "protocol_negotiation.json", _not_attempted_probe(protocol, relay, "not attempted"))

    summary: dict[str, Any] = {
        "run_id": run_id,
        "test_id": test_id,
        "mode": mode,
        "backend": backend.name,
        "requested_protocol": backend.wire_protocol,
        "evaluated_protocol": "draft-ietf-moq-transport-18" if backend.name == "moqt18" else "moq-lite-05",
        "status": "SKIPPED_UNVERIFIED",
        "gate_reasons": [],
        "claim_scope": _claim_scope(mode, backend_name, False),
        "valid_for_protocol_claim": False,
    }

    if mode == "synthetic_plan":
        gate = gate_test(test_id, mode, capability_path, backend_name=backend_name)
        summary["gate_reasons"] = list(gate.reasons)
        pacer = LivePacer()
        media_mode = nested_get(config, "workload.media_mode")
        if media_mode == "synthetic":
            workload = _synthetic_workload(config)
            planned = list(plan_synthetic_objects(workload, run_id, pacer.anchor_ts_ns))
        elif media_mode == "media":
            media_manifest = nested_get(config, "workload.media_manifest")
            if not isinstance(media_manifest, str) or not media_manifest:
                raise ConfigurationError("workload.media_manifest is required for media mode")
            planned = list(reanchor_manifest(read_manifest(Path(media_manifest)), run_id, pacer.anchor_ts_ns))
            workload = None
        else:
            raise ConfigurationError("workload.media_mode must be synthetic or media")
        write_manifest(run_dir / "manifest.jsonl", planned)
        summary["planned_objects"] = len(planned)
        summary["claim_scope"] = _claim_scope(mode, backend_name, False)
        if execute_plan and media_mode == "synthetic":
            assert workload is not None
            events = EventRecorder(run_dir / "events.jsonl")
            publisher = LivePublisher(pacer, events, "local-monotonic", workload.seed)
            publisher.release(planned, lambda _item, _payload: None)
            summary["status"] = "COMPLETED_SYNTHETIC"
        elif execute_plan:
            summary["status"] = "SKIPPED_UNIMPLEMENTED"
            summary["gate_reasons"].append("Media bytes require a verified native adapter; only the fragment manifest was planned.")
        else:
            summary["status"] = "PLANNED_SYNTHETIC"
    elif mode == "event_replay":
        gate = gate_test(test_id, mode, capability_path, backend_name=backend_name)
        summary["status"] = "REPLAY_READY"
        summary["gate_reasons"] = list(gate.reasons)
        summary["claim_scope"] = _claim_scope(mode, backend_name, False)
    else:
        try:
            probe = asyncio.run(backend.probe(relay)).as_dict()
        except BackendError as exc:
            probe = _not_attempted_probe(protocol, relay, str(exc))
        _write_json(run_dir / "protocol_negotiation.json", probe)
        if not probe["success"]:
            summary["status"] = "ABORTED_PROTOCOL_NEGOTIATION"
            summary["gate_reasons"] = [str(probe["reason"])]
        else:
            gate = gate_test(test_id, mode, capability_path, backend_name=backend_name, probe_succeeded=True)
            summary["gate_reasons"] = list(gate.reasons)
            if not gate.runnable:
                summary["status"] = gate.status
            else:
                # No test is marked READY until an independently saved relay
                # media-path smoke proves its mechanism. This guard keeps live
                # traffic from being mistaken for a validated experiment.
                summary["status"] = "SKIPPED_UNIMPLEMENTED"
                summary["gate_reasons"].append("No saved native relay media-path smoke record is registered for this test.")
            summary["claim_scope"] = _claim_scope(mode, backend_name, True)

    provenance = capture_provenance(
        Path.cwd(), config_path, capability_path, protocol, relay, str(summary["claim_scope"])
    )
    _write_json(run_dir / "provenance.json", provenance)
    _write_json(run_dir / "summary.json", summary)
    return run_dir, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan or gate a strict draft-18 MoQT protocol-lab run.")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--capabilities", default=Path("configs/capabilities.example.json"), type=Path)
    parser.add_argument("--results", default=Path("results"), type=Path)
    parser.add_argument("--execute-plan", action="store_true", help="Release a synthetic plan on local monotonic time; this does not send MOQT traffic.")
    args = parser.parse_args(argv)
    try:
        run_dir, summary = run(args.config, args.capabilities, args.results, execute_plan=args.execute_plan)
    except (ConfigurationError, BackendError) as exc:
        parser.error(str(exc))
    print(json.dumps({"run_dir": str(run_dir), **summary}, sort_keys=True))
    return 1 if summary["status"] == "ABORTED_PROTOCOL_NEGOTIATION" else 0


if __name__ == "__main__":
    raise SystemExit(main())
