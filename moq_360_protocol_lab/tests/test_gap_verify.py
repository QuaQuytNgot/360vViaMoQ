import json
import shutil
import csv
from pathlib import Path

from moq_360_protocol_lab.gap_verify import analyze, conditions, guard_set, prepare
from moq_360_protocol_lab.events import EventRecorder
from moq_360_protocol_lab.manifest import read_manifest
from moq_360_protocol_lab.models import Observation


def test_condition_and_guard_model():
    names = {(row["family"], row["name"]) for row in conditions()}
    assert {("G2", "frequency_2hz"), ("G2", "frequency_5hz"),
            ("G2", "frequency_10hz"), ("BASELINES", "short_guard_500")} <= names
    assert guard_set({8, 9, 10, 11, 16, 17, 18, 19}) == {12, 15, 20, 23}


def test_group_deadline_and_skew_are_set_metrics(tmp_path: Path):
    (tmp_path / "configs").mkdir()
    (tmp_path / "results").mkdir()
    source = Path(__file__).resolve().parents[1] / "configs/capabilities.example.json"
    shutil.copy(source, tmp_path / "configs/capabilities.example.json")
    condition = next(row for row in conditions() if row["name"] == "capacity_0.8")
    run = prepare(tmp_path / "results", condition)
    config = json.loads((run / "config.json").read_text())
    items = list(read_manifest(run / "manifest.jsonl"))
    with (run / "subscriber.events.jsonl").open("w") as handle:
        for item in items:
            # One complete set per group. The last tile finishes after the
            # group deadline, while every other tile finishes beforehand.
            offset = 600 if item.track_id == "tile_9" else 100
            timestamp = config["anchor_ns"] + item.group_id * 500_000_000 + offset * 1_000_000
            handle.write(json.dumps(dict(event_type="completed", observed_ts_ns=timestamp,
                track_id=item.track_id, group_id=item.group_id, object_id=item.object_id,
                payload_bytes=item.payload_bytes)) + "\n")
    result = analyze(run)
    assert result["status"] == "INVALID"  # deliberately lacks run artifacts
    assert result["g1"]["complete_groups"] == config["groups"]
    assert result["g1"]["viewport_deadline_miss_ratio"] == 1
    assert result["g1"]["cross_track_skew_ms"]["median"] == 500


def test_relay_timestamps_drive_mixed_window_without_receiver_inference(tmp_path: Path):
    (tmp_path / "results").mkdir()
    condition = next(row for row in conditions() if row["name"] == "overlap_50")
    run = prepare(tmp_path / "results", condition)
    config = json.loads((run / "config.json").read_text())
    anchor = config["anchor_ns"]
    switch = anchor + 2_000_000_000
    events = EventRecorder(run / "request_updates.events.jsonl")
    events.append(Observation("demand_event", switch, "local-monotonic", config["run_id"],
                              "scripted_ground_truth", details={"transition": 0}))
    changed = sorted(set(config["viewport_sets"][0]) ^ set(config["viewport_sets"][1]))
    relay_rows = []
    for i, track in enumerate(changed):
        sent = switch + i * 1_000_000
        request_id = str(i + 100)
        events.append(Observation("request_update_sent", sent, "local-monotonic", config["run_id"],
            "native_REQUEST_UPDATE", track_id=track, details={"transition": 0, "request_id": int(request_id),
            "new_priority": 0 if track in config["viewport_sets"][1] else 255}))
        events.append(Observation("request_ok", sent + 10_000_000, "local-monotonic", config["run_id"],
            "native_REQUEST_UPDATE", track_id=track, details={"transition": 0, "request_id": int(request_id)}))
        for kind, delta in (("UPDATE_RECEIVED", 3), ("UPDATE_APPLIED_TO_REQUEST_STATE", 5),
                            ("FIRST_SCHEDULER_DECISION_NEW_STATE", 20 + i)):
            relay_rows.append(dict(event_type=kind, monotonic_timestamp_ns=sent + delta * 1_000_000,
                                   track_id=track, request_id=request_id))
    with (run / "relay_scheduler_events.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["event_type", "monotonic_timestamp_ns", "track_id", "request_id"])
        writer.writeheader(); writer.writerows(relay_rows)
    result = analyze(run)
    assert result["status"] == "INVALID"  # no native traffic or run monitor
    assert result["transitions"][0]["mixed_epoch_window_ms"] == 14
    assert result["relay_apply_ms"]["median"] == 5
    assert result["scheduler_effect_ms"]["median"] == 23.5
    assert result["receiver_effect_ms"] is None
