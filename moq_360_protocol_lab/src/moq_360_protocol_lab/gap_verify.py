"""Native MOQT gap-verification workload and evidence analysis.

No correlated-set information is sent to the relay. All sets are local demand
labels used for planning and analysis only.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import statistics
import time
import uuid
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from .aiomoqt_d18_update import send_subscriber_priority_update, wait_for_update_response
from .events import EventRecorder, read_events
from .manifest import read_manifest, write_manifest
from .models import Observation
from .p1_adapter import P1ReceiveValidation, _probe_from_session
from .p2_adapter import P2AdapterSettings, _P2PublishedTrack
from .publisher import LivePublisher
from .provenance import capture_provenance
from .subscriber import SubscriberRecorder
from .workload import SyntheticWorkload, plan_synthetic_objects

TRACKS = [f"tile_{i}" for i in range(48)]
A = [8, 9, 10, 11, 16, 17, 18, 19]
B0 = [28, 29, 30, 31, 36, 37, 38, 39]
B50 = [8, 9, 10, 11, 28, 29, 30, 31]
C = [32, 33, 34, 35, 40, 41, 42, 43]
ADJ_B = [9, 10, 11, 12, 17, 18, 19, 20]
ADJ_C = [10, 11, 12, 13, 18, 19, 20, 21]
WEIGHTS = [0.60, 0.72, 0.84, 0.96, 1.04, 1.16, 1.28, 1.40]
RESOURCE_MARKERS = ("Failed to create uni stream", "CrossExecFilter beginSubgroup failed")


def _json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows([{key: row.get(key) for key in fields} for row in rows])


def conditions() -> list[dict]:
    rows = []
    for ratio in (1.0, 0.8, 0.6):
        rows += [dict(family="G1", name=f"capacity_{ratio:g}", rep=rep, capacity_ratio=ratio,
                      group_ms=500, viewport=[f"tile_{i}" for i in A]) for rep in range(1, 6)]
    for size, selected in ((4, A[:4]), (8, A), (12, A + [20, 21, 22, 23])):
        rows += [dict(family="G1", name=f"set_size_{size}", rep=rep, capacity_ratio=0.8,
                      group_ms=500, viewport=[f"tile_{i}" for i in selected], confirmation=True)
                 for rep in range(1, 4)]
    # The confirmation is scheduled by the matrix only if G1 has an effect.
    for overlap, target in ((0, B0), (50, B50)):
        rows += [dict(family="G2", name=f"overlap_{overlap}", rep=rep, capacity_ratio=0.8,
                      group_ms=500, sequence=[A, target], switch_ms=[2000]) for rep in range(1, 4)]
    for hz in (2, 5, 10):
        interval = round(1000 / hz)
        rows += [dict(family="G2", name=f"frequency_{hz}hz", rep=rep, capacity_ratio=0.8,
                      group_ms=500, sequence=[A, B0, C, A, B0, C, A],
                      switch_ms=[2000 + interval * i for i in range(6)], frequency_hz=hz)
                 for rep in range(1, 4)]
    for name, group_ms, guard in (("native_1000", 1000, False), ("short_500", 500, False),
                                  ("short_250", 250, False), ("guard_1000", 1000, True),
                                  ("short_guard_500", 500, True)):
        rows += [dict(family="BASELINES", name=name, rep=rep, capacity_ratio=0.8,
                      group_ms=group_ms, sequence=[A, ADJ_B, ADJ_C, A], switch_ms=[2375, 4375, 6375],
                      guard=guard) for rep in range(1, 4)]
    return rows


def guard_set(viewport: set[int]) -> set[int]:
    # A 6x8 logical panorama with horizontal wrap; one horizontal neighbor
    # on either side of every visible tile. Fixed and deterministic.
    return {row * 8 + (col + delta) % 8 for tile in viewport
            for row, col in [divmod(tile, 8)] for delta in (-1, 1)} - viewport


def prepare(root: Path, condition: dict, *, offered_mbps: float = 8.0) -> Path:
    if condition["family"] not in {"G1", "G2", "BASELINES"}:
        raise ValueError("unknown GAP_VERIFY family")
    if offered_mbps <= 0:
        raise ValueError("offered_mbps must be positive")
    viewport_sets = [set(condition.get("viewport", []))] if condition["family"] == "G1" else [
        {f"tile_{i}" for i in group} for group in condition["sequence"]]
    if any(not group or (len(group) != 8 and not condition.get("confirmation")) or group - set(TRACKS)
           for group in viewport_sets):
        raise ValueError("invalid required viewport tiles")
    active = set().union(*viewport_sets)
    if condition.get("guard"):
        active |= {f"tile_{i}" for group in condition["sequence"] for i in guard_set(set(group))}
    run_id = "run_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
    path = root / "GAP_VERIFY" / condition["family"] / run_id
    path.mkdir(parents=True)
    group_ms = condition["group_ms"]
    object_ms = 50
    groups = max(16, (max(condition.get("switch_ms", [0])) + 2500 + group_ms - 1) // group_ms)
    # Normalize deterministic heterogeneity over the active media Tracks.
    ordered = sorted(active, key=lambda name: int(name.split("_")[1]))
    weights = [WEIGHTS[int(name.split("_")[1]) % len(WEIGHTS)] for name in ordered]
    rates = [round(offered_mbps * 1_000_000 * weight / sum(weights)) for weight in weights]
    workload = SyntheticWorkload(tuple(ordered), tuple(ordered), tuple(rates),
                                 group_ms * 1_000_000, groups, group_ms // object_ms, "gap-verify-v1")
    anchor = time.monotonic_ns() + 15_000_000_000
    planned = [replace(item, logical_media_time_ns=item.logical_media_time_ns + item.object_id * object_ms * 1_000_000,
                       scheduled_publish_ts_ns=item.scheduled_publish_ts_ns + item.object_id * object_ms * 1_000_000)
               for item in plan_synthetic_objects(workload, run_id, anchor)]
    write_manifest(path / "manifest.jsonl", planned)
    config = dict(schema_version=1, family=condition["family"], condition=condition, run_id=run_id,
                  anchor_ns=anchor, group_ms=group_ms, groups=groups, object_ms=object_ms,
                  published_tracks=TRACKS, active_tracks=ordered, viewport_sets=[sorted(s) for s in viewport_sets],
                  offered_mbps=offered_mbps, capacity_mbps=round(offered_mbps * condition["capacity_ratio"], 6),
                  requested_draft=18, transport="raw_quic", alpn="moqt-18",
                  relay_commit="502b6b8f9ddbf4e61f5efe41e92a3d1df40df3a6",
                  relay=dict(publisher_address="10.253.1.1", subscriber_address="10.253.2.1",
                             port=4433, path="/moq-relay", verify_tls=False))
    _json(path / "config.json", config)
    _json(path / "capability_ledger.json", dict(
        requested_draft=18, expected_alpn="moqt-18", relay_commit=config["relay_commit"],
        relay_scheduler_instrumentation=False, controlled_network_verified=False,
        note="Planned capabilities only; negotiate and measure per run."))
    _json(path / "provenance.json", capture_provenance(
        root.parent, path / "config.json", path / "capability_ledger.json",
        {"backend": "moqt18", "draft": 18, "transport": "raw_quic"},
        {"implementation": "moqx", "commit": config["relay_commit"], "port": 4433},
        "GAP_VERIFY controlled native draft-18 stack"))
    return path


async def publisher(path: Path, config: dict, planned: list) -> dict:
    from aiomoqt.client import MOQTClient

    settings = P2AdapterSettings(config["relay"], f"gap-verify/{config['run_id']}", "gap-verify-v1",
                                 "subscriber_0", 20, 60, {}, {}, None, 5)
    events = EventRecorder(path / "publisher.events.jsonl")
    pub = LivePublisher.__new__(LivePublisher)
    pub.recorder, pub.clock_domain_id, pub.seed = events, "local-monotonic", "gap-verify-v1"
    by_track = defaultdict(list)
    for item in planned:
        by_track[item.track_id].append(item)
    async with MOQTClient(**settings.client_kwargs("publisher")).connect() as client:
        await client.client_session_init(timeout=20)
        probe = _probe_from_session(client, settings.relay_evidence("publisher"))
        if not probe.success:
            raise RuntimeError(probe.reason)
        tracks = [_P2PublishedTrack(client, settings.namespace, track, by_track[track], pub, publish_forward=1)
                  for track in TRACKS]
        for track in tracks:
            await track.publish()
        for track in tracks:
            if track.objects:
                track.start()
        await asyncio.wait_for(asyncio.gather(*(track.done.wait() for track in tracks if track.objects)), timeout=90)
        failures = [str(track.failure) for track in tracks if track.failure]
        if failures:
            raise RuntimeError(failures[0])
        marker = path / "subscriber_complete.marker"
        for _ in range(600):
            if marker.exists():
                break
            await asyncio.sleep(0.1)
        client.close()
    return dict(protocol_negotiation_publisher=probe.as_dict(), published_track_count=48,
                media_track_count=len(by_track), published_object_count=len(planned))


async def subscriber(path: Path, config: dict, planned: list) -> dict:
    from aiomoqt.client import MOQTClient
    from aiomoqt.types import FilterType

    settings = P2AdapterSettings(config["relay"], f"gap-verify/{config['run_id']}", "gap-verify-v1",
                                 "subscriber_0", 20, 60, {}, {}, None, 5)
    recorder = SubscriberRecorder("subscriber_0", EventRecorder(path / "subscriber.events.jsonl"), "local-monotonic")
    expected = {(item.track_id, item.group_id, item.object_id): item for item in planned}
    validation = P1ReceiveValidation(expected, "gap-verify-v1", recorder)
    updates = EventRecorder(path / "request_updates.events.jsonl")
    demanded = [set(group) for group in config["viewport_sets"]]
    active = config["active_tracks"]
    def priority_set(required: set[str]) -> set[str]:
        if not config["condition"].get("guard"):
            return required
        return required | {f"tile_{i}" for i in guard_set({int(track.split("_")[1]) for track in required})}
    first = priority_set(demanded[0])
    high = 0
    low = 255
    async with MOQTClient(**settings.client_kwargs("subscriber")).connect() as client:
        await client.client_session_init(timeout=20)
        probe = _probe_from_session(client, settings.relay_evidence("subscriber"))
        if not probe.success:
            raise RuntimeError(probe.reason)
        client.on_object_received = validation.on_object
        ids = {}
        for track in active:
            reply = await client.subscribe(namespace=settings.namespace, track_name=track,
                                           priority=high if track in first else low, forward=1,
                                           filter_type=FilterType.LATEST_OBJECT, wait_response=True)
            ids[track] = reply.request_id
        pending = {}
        async def receive_response(track, sent, transition):
            try:
                _, ts = await wait_for_update_response(client, sent, timeout_s=5)
                kind, detail = "request_ok", {}
            except Exception as exc:
                ts, kind, detail = time.monotonic_ns(), "request_error", {"reason": str(exc)}
            updates.append(Observation(kind, ts, "local-monotonic", config["run_id"],
                "native_REQUEST_UPDATE", track_id=track, correlation_id=f"update-{sent.request_id}",
                details={"transition": transition, "request_id": sent.request_id, **detail}))
            return kind
        for transition, switch_ms in enumerate(config["condition"].get("switch_ms", [])):
            target = config["anchor_ns"] + switch_ms * 1_000_000
            if target > time.monotonic_ns():
                await asyncio.sleep((target - time.monotonic_ns()) / 1e9)
            switch_ts = time.monotonic_ns()
            updates.append(Observation("demand_event", switch_ts, "local-monotonic", config["run_id"],
                "scripted_ground_truth", details={"transition": transition, "required_tracks": sorted(demanded[transition + 1])}))
            old_priority = priority_set(demanded[transition])
            new_priority = priority_set(demanded[transition + 1])
            changed = sorted(old_priority ^ new_priority)
            for track in changed:
                previous = pending.get(track)
                if previous and not previous.done():
                    updates.append(Observation("update_overlap_rejected", time.monotonic_ns(), "local-monotonic",
                        config["run_id"], "native_REQUEST_UPDATE", track_id=track,
                        details={"transition": transition}))
                    continue
                priority = high if track in new_priority else low
                sent = send_subscriber_priority_update(client, ids[track], priority)
                updates.append(Observation("request_update_sent", sent.sent_ts_ns, "local-monotonic",
                    config["run_id"], "native_REQUEST_UPDATE", track_id=track,
                    correlation_id=f"update-{sent.request_id}", details={"transition": transition,
                        "request_id": sent.request_id, "subscription_request_id": sent.subscription_request_id,
                        "old_priority": low if priority == high else high, "new_priority": priority}))
                pending[track] = asyncio.create_task(receive_response(track, sent, transition))
        if pending:
            await asyncio.gather(*pending.values())
        deadline = time.monotonic() + 45
        while len(validation.seen) < len(expected) and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        client.close()
    return dict(protocol_negotiation_subscriber=probe.as_dict(), expected_objects=len(expected),
                received_objects=len(validation.seen), missing_objects=len(expected) - len(validation.seen),
                duplicate_objects=validation.duplicate, malformed_objects=validation.malformed,
                unexpected_objects=validation.unexpected,
                integrity_passed=len(validation.seen) == len(expected) and not any((validation.duplicate,
                    validation.malformed, validation.unexpected)))


def endpoint(path: Path, role: str) -> None:
    config = json.loads((path / "config.json").read_text())
    planned = list(read_manifest(path / "manifest.jsonl"))
    try:
        result = asyncio.run(publisher(path, config, planned) if role == "publisher" else subscriber(path, config, planned))
        _json(path / f"{role}_result.json", result)
        if role == "subscriber":
            (path / "subscriber_complete.marker").touch()
    except BaseException as exc:
        _json(path / f"{role}_failure.json", {"type": type(exc).__name__, "reason": str(exc)})
        raise


def _stats(values: list[float]) -> dict:
    ordered = sorted(values)
    p95 = ordered[min(len(ordered) - 1, max(0, int((len(ordered) * .95 + .999999) - 1)))] if ordered else None
    return dict(n=len(values), median=statistics.median(values) if values else None,
                mean=statistics.mean(values) if values else None,
                stddev=statistics.stdev(values) if len(values) > 1 else None,
                min=min(values) if values else None, max=max(values) if values else None, p95=p95)


def analyze(path: Path) -> dict:
    config = json.loads((path / "config.json").read_text())
    plan = list(read_manifest(path / "manifest.jsonl"))
    events = list(read_events(path / "subscriber.events.jsonl")) if (path / "subscriber.events.jsonl").exists() else []
    updates = list(read_events(path / "request_updates.events.jsonl")) if (path / "request_updates.events.jsonl").exists() else []
    relay_events_path = path / "relay_scheduler_events.csv"
    relay_events = list(csv.DictReader(relay_events_path.open())) if relay_events_path.exists() else []
    receives = [e for e in events if e["event_type"] == "completed"]
    received = defaultdict(list)
    for event in receives:
        received[(event["track_id"], event["group_id"])].append(event)
    planned = defaultdict(list)
    for item in plan:
        planned[(item.track_id, item.group_id)].append(item)
    track_rows = []
    for (track, group), items in sorted(planned.items()):
        rows = received[(track, group)]
        ids = {row["object_id"] for row in rows}
        complete = ids == {item.object_id for item in items}
        track_rows.append(dict(track_id=track, group_id=group, expected_objects=len(items), received_objects=len(ids),
            first_object_ns=min((row["observed_ts_ns"] for row in rows), default=None),
            last_object_ns=max((row["observed_ts_ns"] for row in rows), default=None),
            completion_ns=max((row["observed_ts_ns"] for row in rows), default=None) if complete else None,
            source_ns=min(item.scheduled_publish_ts_ns for item in items), complete=complete))
    _csv(path / "track_group_completion.csv", track_rows, list(track_rows[0]) if track_rows else ["track_id", "group_id"])
    demands = sorted((e for e in updates if e["event_type"] == "demand_event"), key=lambda e: e["observed_ts_ns"])
    viewport_rows = []
    for group in range(config["groups"]):
        group_start = config["anchor_ns"] + group * config["group_ms"] * 1_000_000
        index = sum(event["observed_ts_ns"] <= group_start for event in demands)
        required = set(config["viewport_sets"][index])
        rows = [row for row in track_rows if row["group_id"] == group and row["track_id"] in required]
        completions = [row["completion_ns"] for row in rows]
        full = len(rows) == len(required) and all(value is not None for value in completions)
        complete_ns = max(completions) if full else None
        deadline = group_start + config["group_ms"] * 1_000_000
        viewport_rows.append(dict(group_id=group, required_tracks=json.dumps(sorted(required)),
            required_count=len(required), complete=full, completion_ns=complete_ns,
            completion_latency_ms=(complete_ns - group_start) / 1e6 if full else None,
            deadline_ns=deadline, deadline_miss=not full or complete_ns > deadline,
            skew_ms=(max(completions) - min(completions)) / 1e6 if full else None))
    _csv(path / "viewport_groups.csv", viewport_rows, list(viewport_rows[0]))
    g1 = dict(viewport_completion_latency_ms=_stats([r["completion_latency_ms"] for r in viewport_rows if r["complete"]]),
              viewport_deadline_miss_ratio=sum(r["deadline_miss"] for r in viewport_rows) / len(viewport_rows),
              cross_track_skew_ms=_stats([r["skew_ms"] for r in viewport_rows if r["complete"]]),
              complete_groups=sum(r["complete"] for r in viewport_rows), total_groups=len(viewport_rows))
    duration_s = config["groups"] * config["group_ms"] / 1000
    by_track_bytes = {track: sum((row.get("payload_bytes") or 0) for row in receives if row["track_id"] == track)
                      for track in config["active_tracks"]}
    g1["aggregate_goodput_mbps"] = sum(by_track_bytes.values()) * 8 / duration_s / 1e6
    g1["weakest_track_goodput_mbps"] = min(by_track_bytes.values()) * 8 / duration_s / 1e6 if by_track_bytes else None
    sent = {(e["details"]["transition"], e["track_id"]): e for e in updates if e["event_type"] == "request_update_sent"}
    ack = {(e["details"]["transition"], e["track_id"]): e for e in updates if e["event_type"] == "request_ok"}
    outstanding_events = [(event["observed_ts_ns"], 1) for event in sent.values()] + [
        (event["observed_ts_ns"], -1) for event in updates if event["event_type"] in {"request_ok", "request_error"}]
    outstanding = peak_outstanding = 0
    for _, delta in sorted(outstanding_events):
        outstanding += delta
        peak_outstanding = max(peak_outstanding, outstanding)
    per_update = []
    for (transition, track), event in sorted(sent.items()):
        reply = ack.get((transition, track))
        switch = demands[transition]["observed_ts_ns"]
        new = event["details"]["new_priority"]
        # A receiver event is only a proxy. No scheduler effect is inferred.
        first_rx = min((e["observed_ts_ns"] for e in receives if e["track_id"] == track
                        and e["observed_ts_ns"] >= switch), default=None)
        next_switch = demands[transition + 1]["observed_ts_ns"] if transition + 1 < len(demands) else None
        old_delivery = [e for e in receives if e["track_id"] == track and
                        (next_switch is None or e["observed_ts_ns"] < next_switch)] if new == 255 else []
        sent_ns = event["observed_ts_ns"]
        ack_ns = reply["observed_ts_ns"] if reply else None
        request_id = str(event["details"]["request_id"])
        relevant = [row for row in relay_events if row.get("track_id") == track and row.get("request_id") == request_id]
        def relay_ts(kind: str) -> int | None:
            values = [int(row["monotonic_timestamp_ns"]) for row in relevant
                      if row.get("event_type") == kind and (row.get("monotonic_timestamp_ns") or "").isdigit()]
            return min(values) if values else None
        received_ns = relay_ts("UPDATE_RECEIVED")
        applied_ns = relay_ts("UPDATE_APPLIED_TO_REQUEST_STATE")
        selected_ns = relay_ts("FIRST_SCHEDULER_DECISION_NEW_STATE")
        per_update.append(dict(transition=transition, track_id=track, new_priority=new,
            send_ns=sent_ns, ack_ns=ack_ns,
            control_ms=(reply["observed_ts_ns"] - event["observed_ts_ns"]) / 1e6 if reply else None,
            relay_received_ns=received_ns, relay_apply_ns=applied_ns, scheduler_effect_ns=selected_ns,
            relay_apply_ms=(applied_ns - sent_ns) / 1e6 if applied_ns else None,
            scheduler_effect_ms=(selected_ns - sent_ns) / 1e6 if selected_ns else None,
            receiver_effect_ms=None,
            first_receiver_object_ms=(first_rx - event["observed_ts_ns"]) / 1e6 if first_rx else None,
            old_state_objects_after_update=sum(e["observed_ts_ns"] > sent_ns for e in old_delivery),
            old_state_bytes_after_update=sum((e.get("payload_bytes") or 0) for e in old_delivery if e["observed_ts_ns"] > sent_ns),
            old_state_objects_after_ack=sum(e["observed_ts_ns"] > ack_ns for e in old_delivery) if ack_ns else None,
            old_state_bytes_after_ack=sum((e.get("payload_bytes") or 0) for e in old_delivery if e["observed_ts_ns"] > ack_ns) if ack_ns else None,
            old_state_objects_after_scheduler_effect=sum(e["observed_ts_ns"] > selected_ns for e in old_delivery)
                if selected_ns else None,
            old_state_bytes_after_scheduler_effect=sum((e.get("payload_bytes") or 0) for e in old_delivery
                                                       if e["observed_ts_ns"] > selected_ns) if selected_ns else None))
    _csv(path / "per_track_updates.csv", per_update, ["transition", "track_id", "new_priority", "send_ns",
        "ack_ns", "control_ms", "relay_received_ns", "relay_apply_ns", "scheduler_effect_ns",
        "relay_apply_ms", "scheduler_effect_ms", "receiver_effect_ms", "first_receiver_object_ms",
        "old_state_objects_after_update", "old_state_bytes_after_update", "old_state_objects_after_ack",
        "old_state_bytes_after_ack", "old_state_objects_after_scheduler_effect", "old_state_bytes_after_scheduler_effect"])
    transitions = []
    for index, demand in enumerate(demands):
        ts = demand["observed_ts_ns"]
        old = set(config["viewport_sets"][index])
        new = set(config["viewport_sets"][index + 1])
        leaving = old - new
        next_switch = demands[index + 1]["observed_ts_ns"] if index + 1 < len(demands) else None
        old_rows = [row for row in receives if row["track_id"] in leaving and
                    (next_switch is None or row["observed_ts_ns"] < next_switch)]
        all_acks = [row["ack_ns"] for row in per_update if row["transition"] == index and row["ack_ns"]]
        update_rows = [row for row in per_update if row["transition"] == index]
        relay_receipts = [row["relay_received_ns"] for row in update_rows if row["relay_received_ns"]]
        scheduler_effects = [row["scheduler_effect_ns"] for row in update_rows if row["scheduler_effect_ns"]]
        first_new = min((row["observed_ts_ns"] for row in receives if row["track_id"] in new and
                         row["observed_ts_ns"] >= ts), default=None)
        complete = min((row for row in viewport_rows if row["complete"] and
                        row["completion_ns"] >= ts and set(json.loads(row["required_tracks"])) == new),
                       key=lambda row: row["completion_ns"], default=None)
        transitions.append(dict(transition=index, switch_ns=ts, changed_tracks=len(update_rows),
            first_send_ns=min((row["send_ns"] for row in per_update if row["transition"] == index), default=None),
            last_send_ns=max((row["send_ns"] for row in per_update if row["transition"] == index), default=None),
            first_relay_update_received_ns=min(relay_receipts) if relay_receipts else None,
            last_relay_update_received_ns=max(relay_receipts) if relay_receipts else None,
            first_ack_ns=min(all_acks) if all_acks else None, last_ack_ns=max(all_acks) if all_acks else None,
            first_scheduler_effect_ns=min(scheduler_effects) if scheduler_effects else None,
            last_scheduler_effect_ns=max(scheduler_effects) if scheduler_effects else None,
            first_receiver_new_object_ns=first_new,
            first_complete_new_group_ns=complete["completion_ns"] if complete else None,
            transition_latency_ms=(complete["completion_ns"] - ts) / 1e6 if complete else None,
            old_viewport_bytes_after_switch=sum(row.get("payload_bytes") or 0 for row in old_rows if row["observed_ts_ns"] > ts),
            old_viewport_bytes_after_all_acks=sum(row.get("payload_bytes") or 0 for row in old_rows
                                                  if row["observed_ts_ns"] > max(all_acks))
                                                  if len(all_acks) == len(update_rows) and all_acks else None,
            mixed_epoch_window_ms=(max(scheduler_effects) - min(scheduler_effects)) / 1e6
                if len(scheduler_effects) == len(update_rows) and scheduler_effects else None,
            old_viewport_bytes_after_scheduler_effect=sum(row.get("payload_bytes") or 0 for row in old_rows
                if scheduler_effects and len(scheduler_effects) == len(update_rows)
                and row["observed_ts_ns"] > max(scheduler_effects))
                if len(scheduler_effects) == len(update_rows) and scheduler_effects else None))
    _csv(path / "transitions.csv", transitions, ["transition", "switch_ns", "changed_tracks", "first_send_ns",
        "last_send_ns", "first_relay_update_received_ns", "last_relay_update_received_ns",
        "first_ack_ns", "last_ack_ns", "first_scheduler_effect_ns", "last_scheduler_effect_ns", "first_receiver_new_object_ns",
        "first_complete_new_group_ns", "transition_latency_ms", "old_viewport_bytes_after_switch",
        "old_viewport_bytes_after_all_acks", "mixed_epoch_window_ms", "old_viewport_bytes_after_scheduler_effect"])
    relay_log = (path / "relay.log").read_text(errors="replace") if (path / "relay.log").exists() else ""
    resource_errors = [marker for marker in RESOURCE_MARKERS if marker in relay_log]
    endpoint_results = {}
    for role in ("publisher", "subscriber"):
        source = path / f"{role}_result.json"
        endpoint_results[role] = json.loads(source.read_text()) if source.exists() else None
        if endpoint_results[role]:
            _json(path / f"protocol_negotiation_{role}.json",
                  endpoint_results[role].get(f"protocol_negotiation_{role}"))
    monitor = list(csv.DictReader((path / "monitor.csv").open())) if (path / "monitor.csv").exists() else []
    monitor_ok = (bool(monitor) and all(row.get("relay_alive") == "1" for row in monitor)
                  and any(row.get("publisher_alive") == "1" for row in monitor)
                  and any(row.get("subscriber_alive") == "1" for row in monitor))
    negotiated = all(endpoint_results.get(role) and endpoint_results[role].get(f"protocol_negotiation_{role}", {}).get("success")
                     for role in ("publisher", "subscriber"))
    # G2 requires actual relay scheduler instrumentation. Receiver arrivals do
    # not establish when the relay applied or selected a new priority.
    required_relay_types = {"OBJECT_SUBMITTED"} if config["family"] == "G1" else {
        "UPDATE_RECEIVED", "UPDATE_APPLIED_TO_REQUEST_STATE", "REQUEST_OK_SENT",
        "FIRST_SCHEDULER_DECISION_NEW_STATE", "OBJECT_SUBMITTED"}
    instrumentation_ok = required_relay_types <= {row.get("event_type") for row in relay_events}
    instrumentation_ok = instrumentation_ok and sum(row.get("event_type") == "OBJECT_SUBMITTED"
                                                  for row in relay_events) >= len(plan)
    if config["family"] != "G1":
        instrumentation_ok = instrumentation_ok and bool(per_update) and all(
            row["relay_received_ns"] and row["relay_apply_ns"] and row["scheduler_effect_ns"]
            for row in per_update)
    def priority_set(required: set[str]) -> set[str]:
        if not config["condition"].get("guard"):
            return required
        return required | {f"tile_{i}" for i in guard_set({int(track.split("_")[1]) for track in required})}
    intended_updates = sum(len(priority_set(set(config["viewport_sets"][i])) ^
                               priority_set(set(config["viewport_sets"][i + 1])))
                           for i in range(len(demands)))
    updates_complete = len(sent) == intended_updates and len(ack) == intended_updates and not any(
        e["event_type"] in {"request_error", "update_overlap_rejected"} for e in updates)
    artifacts_complete = all((path / name).exists() for name in (
        "provenance.json", "relay.log", "monitor.csv", "monitor.jsonl",
        "network_publisher_before.json", "network_publisher_after.json",
        "network_relay_sub_before.json", "network_relay_sub_after.json"))
    valid = bool(negotiated and endpoint_results["subscriber"]["integrity_passed"] and monitor_ok and
                 not resource_errors and (not config["condition"].get("switch_ms") or
                 len(demands) == len(config["condition"]["switch_ms"])) and instrumentation_ok and
                 updates_complete and artifacts_complete)
    def required_at(ts: int) -> set[str]:
        index = sum(event["observed_ts_ns"] <= ts for event in demands)
        return set(config["viewport_sets"][index])
    useful_bytes = sum((row.get("payload_bytes") or 0) for row in receives
                       if row["track_id"] in required_at(row["observed_ts_ns"]))
    non_required_bytes = sum((row.get("payload_bytes") or 0) for row in receives
                             if row["track_id"] not in required_at(row["observed_ts_ns"]))
    def is_guard(row: dict) -> bool:
        required = required_at(row["observed_ts_ns"])
        guard = guard_set({int(track.split("_")[1]) for track in required})
        return row["track_id"] in {f"tile_{tile}" for tile in guard}
    guard_rows = [row for row in receives if config["condition"].get("guard") and is_guard(row)]
    guard_bytes = sum(row.get("payload_bytes") or 0 for row in guard_rows)
    def used_after_switch(row: dict) -> bool:
        ts = row["observed_ts_ns"]
        group = row["group_id"]
        start = config["anchor_ns"] + group * config["group_ms"] * 1_000_000
        end = start + config["group_ms"] * 1_000_000
        return any(ts < event["observed_ts_ns"] < end and start <= event["observed_ts_ns"]
                   and row["track_id"] in config["viewport_sets"][index + 1]
                   for index, event in enumerate(demands))
    used_guard_bytes = sum(row.get("payload_bytes") or 0 for row in guard_rows if used_after_switch(row))
    delivered_bytes = useful_bytes + non_required_bytes
    summary = dict(run_id=config["run_id"], family=config["family"], condition=config["condition"],
                   status="VALID" if valid else "INVALID", valid=valid, g1=g1, transitions=transitions,
                   control_ms=_stats([row["control_ms"] for row in per_update if row["control_ms"] is not None]),
                   relay_apply_ms=_stats([row["relay_apply_ms"] for row in per_update if row["relay_apply_ms"] is not None]),
                   scheduler_effect_ms=_stats([row["scheduler_effect_ms"] for row in per_update if row["scheduler_effect_ms"] is not None]),
                   receiver_effect_ms=None,
                   useful_required_bytes=useful_bytes, non_required_bytes=non_required_bytes,
                   guard_bytes=guard_bytes, guard_bytes_potentially_used_after_switch=used_guard_bytes,
                   guard_bytes_never_used=guard_bytes - used_guard_bytes,
                   overfetch_ratio=non_required_bytes / delivered_bytes if delivered_bytes else None,
                   instrumentation_available=instrumentation_ok,
                   intended_updates=intended_updates, sent_updates=len(sent), acked_updates=len(ack),
                   peak_outstanding_updates=peak_outstanding,
                   update_overlap_rejected=sum(e["event_type"] == "update_overlap_rejected" for e in updates),
                   failed_updates=sum(e["event_type"] == "request_error" for e in updates),
                   artifacts_complete=artifacts_complete,
                   resource_errors=resource_errors, monitor_samples=len(monitor),
                   monitor_peak_relay_cpu_percent=max((float(row.get("relay_cpu_percent") or 0) for row in monitor), default=None),
                   monitor_peak_relay_rss_bytes=max((int(row.get("relay_rss_bytes") or 0) for row in monitor), default=None),
                   protocol_negotiation={role: endpoint_results[role].get(f"protocol_negotiation_{role}")
                                         if endpoint_results[role] else None for role in endpoint_results})
    _json(path / "summary.json", summary)
    return summary


def aggregate(root: Path) -> None:
    base = root / "GAP_VERIFY"
    base.mkdir(parents=True, exist_ok=True)
    summaries = {}
    for source in base.glob("*/run_*/summary.json"):
        try:
            record = json.loads(source.read_text())
            key = (record["family"], record["condition"]["name"], record["condition"]["rep"])
            summaries[key] = record
        except (ValueError, KeyError):
            continue
    rows = []
    for condition in conditions():
        key = (condition["family"], condition["name"], condition["rep"])
        record = summaries.get(key)
        g1 = record.get("g1", {}) if record else {}
        rows.append(dict(family=key[0], condition=key[1], repetition=key[2],
            status=record["status"] if record else "NOT_RUN", run_id=record["run_id"] if record else None,
            capacity_ratio=condition["capacity_ratio"], group_duration_ms=condition["group_ms"],
            required_tracks=len(condition.get("viewport", condition.get("sequence", [[]])[0])),
            viewport_completion_latency_median_ms=g1.get("viewport_completion_latency_ms", {}).get("median"),
            viewport_deadline_miss_ratio=g1.get("viewport_deadline_miss_ratio"),
            cross_track_skew_median_ms=g1.get("cross_track_skew_ms", {}).get("median"),
            control_ack_median_ms=record.get("control_ms", {}).get("median") if record else None,
            relay_apply_median_ms=record.get("relay_apply_ms", {}).get("median") if record else None,
            scheduler_effect_median_ms=record.get("scheduler_effect_ms", {}).get("median") if record else None,
            mixed_epoch_window_ms=statistics.median([t["mixed_epoch_window_ms"] for t in record["transitions"]
                if t["mixed_epoch_window_ms"] is not None]) if record and any(
                t["mixed_epoch_window_ms"] is not None for t in record["transitions"]) else None,
            old_viewport_bytes_after_switch=sum(t["old_viewport_bytes_after_switch"] for t in record["transitions"])
                if record else None,
            overfetch_ratio=record.get("overfetch_ratio") if record else None))
    fields = list(rows[0])
    _csv(base / "gap_verify_summary.csv", rows, fields)
    condition_stats = []
    for family, name in sorted({(row["family"], row["condition"]) for row in rows}):
        valid_rows = [row for row in rows if row["family"] == family and row["condition"] == name
                      and row["status"] == "VALID"]
        condition_stats.append(dict(family=family, condition=name, valid_repetitions=len(valid_rows),
            viewport_completion_latency_median_ms=_stats([row["viewport_completion_latency_median_ms"]
                for row in valid_rows if row["viewport_completion_latency_median_ms"] is not None]),
            viewport_deadline_miss_ratio=_stats([row["viewport_deadline_miss_ratio"]
                for row in valid_rows if row["viewport_deadline_miss_ratio"] is not None]),
            cross_track_skew_median_ms=_stats([row["cross_track_skew_median_ms"]
                for row in valid_rows if row["cross_track_skew_median_ms"] is not None])))
    _json(base / "gap_verify_summary.json", dict(schema_version=1, runs=rows,
        condition_statistics=condition_stats,
        valid_runs=sum(row["status"] == "VALID" for row in rows),
        classifications=dict(G1=None, G2_CONTROL_DATA_SEPARATION_OBSERVED=None,
                             G2_MULTI_TRACK_NONATOMICITY_OBSERVED=None,
                             G2_APPLICATION_IMPACT_OBSERVED=None),
        evidence_note="Null means unmeasured; NOT_RUN is never evidence of absence."))


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--root", type=Path, required=True)
    prep.add_argument("--family", required=True)
    prep.add_argument("--name", required=True)
    prep.add_argument("--rep", type=int, required=True)
    prep.add_argument("--offered-mbps", type=float, default=8)
    ep = sub.add_parser("endpoint")
    ep.add_argument("--run-dir", type=Path, required=True)
    ep.add_argument("--role", choices=("publisher", "subscriber"), required=True)
    final = sub.add_parser("finalize")
    final.add_argument("--run-dir", type=Path, required=True)
    agg = sub.add_parser("aggregate")
    agg.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        condition = next((row for row in conditions() if (row["family"], row["name"], row["rep"]) ==
                          (args.family, args.name, args.rep)), None)
        if condition is None:
            parser.error("condition not found")
        print(prepare(args.root, condition, offered_mbps=args.offered_mbps))
    elif args.command == "endpoint":
        endpoint(args.run_dir, args.role)
    elif args.command == "aggregate":
        aggregate(args.root)
    else:
        print(json.dumps(analyze(args.run_dir)))


if __name__ == "__main__":
    main()
