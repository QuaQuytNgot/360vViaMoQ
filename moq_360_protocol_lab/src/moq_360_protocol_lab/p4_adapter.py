"""Native draft-18 P4 Forward-state adapter.

This adapter deliberately has no relay policy.  It keeps a deterministic live
source running at wall-clock media times, sends initial Forward on SUBSCRIBE,
and uses the P2-established REQUEST_UPDATE sender for later transitions.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .aiomoqt_d18_update import RequestUpdateSent, send_forward_update, wait_for_update_response
from .events import EventRecorder
from .models import Observation, PlannedObject
from .p1_adapter import P1AdapterError, P1ReceiveValidation, _probe_from_session
from .p2_adapter import Moqt18P2Adapter, P2AdapterSettings, _P2PublishedTrack
from .subscriber import SubscriberRecorder


@dataclass(frozen=True)
class P4User:
    user_id: str
    initial_forward_tracks: tuple[str, ...]
    demand_forward_tracks: tuple[str, ...]
    deactivate_tracks: tuple[str, ...]


@dataclass(frozen=True)
class P4AdapterSettings(P2AdapterSettings):
    mode: str = "dormant"
    demand_at_ms: int = 0
    deactivate_at_ms: int | None = None
    observation_after_demand_ms: int = 1000
    overlap_label: str = "not_applicable"
    users: tuple[P4User, ...] = ()

    @classmethod
    def from_config(cls, config: Mapping[str, Any], *, namespace: str) -> "P4AdapterSettings":
        p4, workload, runtime, relay = (config.get(k) for k in ("p4", "workload", "runtime", "relay"))
        if not all(isinstance(v, Mapping) for v in (p4, workload, runtime, relay)):
            raise P1AdapterError("P4 requires p4, workload, runtime, and relay mappings")
        assert isinstance(p4, Mapping) and isinstance(workload, Mapping) and isinstance(runtime, Mapping) and isinstance(relay, Mapping)
        tracks = workload.get("track_ids")
        if not isinstance(tracks, list) or not tracks or not all(isinstance(x, str) for x in tracks):
            raise P1AdapterError("P4 workload.track_ids must be a non-empty string list")
        mode = p4.get("mode")
        if mode not in {"forward_f1", "forward_f0", "forward_0_to_1", "forward_1_to_0", "reactive", "dormant", "fanout"}:
            raise P1AdapterError("p4.mode is not a supported native Forward characterization mode")
        demand_at_ms = p4.get("demand_at_ms", 0)
        if not isinstance(demand_at_ms, int) or demand_at_ms < 0:
            raise P1AdapterError("p4.demand_at_ms must be a non-negative integer")
        deactivate_at_ms = p4.get("deactivate_at_ms")
        if deactivate_at_ms is not None and (not isinstance(deactivate_at_ms, int) or deactivate_at_ms < demand_at_ms):
            raise P1AdapterError("p4.deactivate_at_ms must be absent or >= demand_at_ms")
        raw_users = p4.get("users")
        if raw_users is None:
            raw_users = [{"id": str(runtime.get("subscriber_id", "subscriber_0"))}]
        if not isinstance(raw_users, list) or not raw_users:
            raise P1AdapterError("p4.users must be a non-empty list")
        users: list[P4User] = []
        for index, raw in enumerate(raw_users):
            if not isinstance(raw, Mapping) or not isinstance(raw.get("id", f"user_{index}"), str):
                raise P1AdapterError("each p4 user requires a string id")
            uid = str(raw.get("id", f"user_{index}"))
            def selected(key: str, default: list[str]) -> tuple[str, ...]:
                value = raw.get(key, default)
                if not isinstance(value, list) or not all(isinstance(item, str) and item in tracks for item in value):
                    raise P1AdapterError(f"p4.users[{uid}].{key} must contain known track IDs")
                return tuple(value)
            initial_default = tracks if mode in {"forward_f1", "forward_1_to_0"} else []
            demand_default = tracks if mode in {"forward_0_to_1", "reactive", "dormant", "fanout"} else []
            deactivate_default = tracks if mode == "forward_1_to_0" else []
            users.append(P4User(uid, selected("initial_forward_tracks", initial_default),
                                selected("demand_forward_tracks", demand_default), selected("deactivate_tracks", deactivate_default)))
        if len({user.user_id for user in users}) != len(users):
            raise P1AdapterError("p4 user IDs must be unique")
        return cls(dict(relay), namespace, str(workload.get("seed", "")), str(runtime.get("subscriber_id", "subscriber_0")),
                   float(runtime.get("connect_timeout_s", 15)), float(runtime.get("receive_timeout_s", 30)),
                   {track: 128 for track in tracks}, {}, None, float(p4.get("update_timeout_s", 5)), mode,
                   demand_at_ms, deactivate_at_ms, int(p4.get("observation_after_demand_ms", 1000)),
                   str(p4.get("overlap", "not_applicable")), tuple(users))


class Moqt18P4Adapter(Moqt18P2Adapter):
    """P4 endpoint roles; P2's publisher framing remains the native data path."""

    settings: P4AdapterSettings

    def __init__(self, settings: P4AdapterSettings, publisher_events: Path, subscriber_dir: Path, update_events: Path):
        super().__init__(settings, publisher_events, subscriber_dir / "unused.events.jsonl", update_events)
        self.subscriber_dir = subscriber_dir
        self.phases = EventRecorder(subscriber_dir / "run_phases.events.jsonl")

    def _phase(self, run_id: str, phase: str, *, role: str, user_id: str | None = None, **details: Any) -> int:
        timestamp = time.monotonic_ns()
        self.phases.append(Observation("run_phase", timestamp, "local-monotonic", run_id,
            "p4_harness_lifecycle", user_id=user_id, native_operation=phase,
            details={"phase": phase, "role": role, **details}))
        return timestamp

    async def run_publisher(self, planned: Iterable[PlannedObject], *, completion_marker: Path | None = None) -> dict[str, Any]:
        from aiomoqt.client import MOQTClient
        objects, grouped, _ = self._objects(planned, self.settings.seed)
        relay = self.settings.relay_evidence("publisher")
        started = time.monotonic_ns()
        async with MOQTClient(**self.settings.client_kwargs("publisher")).connect() as client:
            async with asyncio.timeout(self.settings.connect_timeout_s):
                await client.client_session_init(timeout=max(1, int(self.settings.connect_timeout_s)))
            probe = _probe_from_session(client, relay)
            if not probe.success:
                raise P1AdapterError(f"publisher protocol negotiation failed: {probe.reason}")
            session_established = self._phase(objects[0].run_id, "SESSION_ESTABLISHED", role="publisher")
            tracks = [_P2PublishedTrack(client, self.settings.namespace, track, values, self.publisher, publish_forward=1)
                      for track, values in sorted(grouped.items())]
            for track in tracks:
                # A P4 source is live independent of downstream demand. This
                # is not a relay cache/pre-warm action.
                await track.publish()
                track.start()
            measurement_start = self._phase(objects[0].run_id, "MEASUREMENT_START", role="publisher")
            async with asyncio.timeout(self.settings.receive_timeout_s):
                await asyncio.gather(*(track.done.wait() for track in tracks))
            failures = [track.failure for track in tracks if track.failure]
            if failures:
                raise P1AdapterError(f"P4 publisher failed: {failures[0]}")
            if completion_marker is not None:
                deadline = time.monotonic() + self.settings.receive_timeout_s
                while not completion_marker.exists() and time.monotonic() < deadline:
                    await asyncio.sleep(0.05)
            self._phase(objects[0].run_id, "MEASUREMENT_END", role="publisher")
            teardown_start = self._phase(objects[0].run_id, "ENDPOINT_TEARDOWN_START", role="publisher")
            client.close()
        teardown_complete = self._phase(objects[0].run_id, "ENDPOINT_TEARDOWN_COMPLETE", role="publisher")
        return {"protocol_negotiation_publisher": probe.as_dict(), "started_ts_ns": started,
                "ended_ts_ns": time.monotonic_ns(), "published_track_count": len(tracks),
                "published_object_count": len(objects), "session_established_ts_ns": session_established,
                "measurement_start_ts_ns": measurement_start, "teardown_start_ts_ns": teardown_start,
                "teardown_complete_ts_ns": teardown_complete}

    async def _update(self, client: Any, subscription_ids: Mapping[str, int], tracks: Iterable[str], forward: int,
                      user: P4User, run_id: str, phase: str) -> list[dict[str, Any]]:
        records: list[tuple[str, RequestUpdateSent]] = []
        for track in sorted(set(tracks)):
            generated = time.monotonic_ns()
            sent = send_forward_update(client, subscription_ids[track], forward)
            self.updates.append(Observation("request_update_sent", sent.sent_ts_ns, "local-monotonic", run_id,
                "aiomoqt_draft18_request_update", user_id=user.user_id, track_id=track,
                correlation_id=f"update-{sent.request_id}", native_operation="send_stream_message",
                details={"phase": phase, "generated_ts_ns": generated, "encode_ts_ns": sent.encode_ts_ns,
                         "request_id": sent.request_id, "subscription_request_id": sent.subscription_request_id, "forward": forward}))
            records.append((track, sent))
        output: list[dict[str, Any]] = []
        for track, sent in records:
            try:
                _reply, received = await wait_for_update_response(client, sent, timeout_s=self.settings.update_timeout_s)
                event, details = "request_ok", {"phase": phase, "request_id": sent.request_id,
                    "forward": forward, "control_response_latency_ns": received - sent.sent_ts_ns}
            except Exception as exc:
                received, event, details = time.monotonic_ns(), "request_error", {"phase": phase,
                    "request_id": sent.request_id, "forward": forward, "reason": str(exc)}
            self.updates.append(Observation(event, received, "local-monotonic", run_id, "aiomoqt_draft18_request_update",
                user_id=user.user_id, track_id=track, correlation_id=f"update-{sent.request_id}",
                native_operation="REQUEST_OK/REQUEST_ERROR", details=details))
            output.append({"track_id": track, "event_type": event, "sent_ts_ns": sent.sent_ts_ns, "received_ts_ns": received})
        return output

    async def _run_user(self, user: P4User, objects: list[PlannedObject], grouped: Mapping[str, list[PlannedObject]],
                        expected: Mapping[tuple[str, int, int], PlannedObject], anchor_ns: int) -> dict[str, Any]:
        from aiomoqt.client import MOQTClient
        from aiomoqt.types import FilterType
        recorder = SubscriberRecorder(user.user_id, EventRecorder(self.subscriber_dir / f"subscriber_{user.user_id}.events.jsonl"), "local-monotonic")
        validation = P1ReceiveValidation(dict(expected), self.settings.seed, recorder)
        relay = self.settings.relay_evidence("subscriber")
        started = time.monotonic_ns()
        demand_ts: int | None = None
        async with MOQTClient(**self.settings.client_kwargs("subscriber")).connect() as client:
            async with asyncio.timeout(self.settings.connect_timeout_s):
                await client.client_session_init(timeout=max(1, int(self.settings.connect_timeout_s)))
            probe = _probe_from_session(client, relay)
            if not probe.success:
                raise P1AdapterError(f"subscriber protocol negotiation failed for {user.user_id}: {probe.reason}")
            session_established = self._phase(objects[0].run_id, "SESSION_ESTABLISHED", role="subscriber", user_id=user.user_id)
            client.on_object_received = validation.on_object
            subscription_ids: dict[str, int] = {}
            if self.settings.mode != "reactive":
                for track in sorted(grouped):
                    response = await client.subscribe(namespace=self.settings.namespace, track_name=track, priority=128,
                        forward=int(track in user.initial_forward_tracks), filter_type=FilterType.LATEST_OBJECT, wait_response=True)
                    request_id = getattr(response, "request_id", None)
                    if not isinstance(request_id, int):
                        raise P1AdapterError(f"SUBSCRIBE response lacked request ID for {track}")
                    subscription_ids[track] = request_id
            measurement_start = self._phase(objects[0].run_id, "MEASUREMENT_START", role="subscriber", user_id=user.user_id)
            if self.settings.mode in {"forward_0_to_1", "reactive", "dormant", "fanout"}:
                target = anchor_ns + self.settings.demand_at_ms * 1_000_000
                if target > time.monotonic_ns():
                    await asyncio.sleep((target - time.monotonic_ns()) / 1_000_000_000)
                demand_ts = time.monotonic_ns()
                self.updates.append(Observation("demand_event", demand_ts, "local-monotonic", objects[0].run_id,
                    "p4_deterministic_synthetic_demand", user_id=user.user_id, native_operation="deterministic_demand_schedule",
                    details={"mode": self.settings.mode, "forward_tracks": list(user.demand_forward_tracks)}))
                if self.settings.mode == "reactive":
                    for track in sorted(user.demand_forward_tracks):
                        response = await client.subscribe(namespace=self.settings.namespace, track_name=track, priority=128,
                            forward=1, filter_type=FilterType.LATEST_OBJECT, wait_response=True)
                        request_id = getattr(response, "request_id", None)
                        if not isinstance(request_id, int):
                            raise P1AdapterError(f"reactive SUBSCRIBE response lacked request ID for {track}")
                        subscription_ids[track] = request_id
                        self.updates.append(Observation("subscribe_ok", time.monotonic_ns(), "local-monotonic", objects[0].run_id,
                            "aiomoqt_draft18_subscribe", user_id=user.user_id, track_id=track,
                            correlation_id=f"subscribe-{request_id}", native_operation="SUBSCRIBE_OK",
                            details={"phase": "activate", "request_id": request_id, "forward": 1}))
                else:
                    await self._update(client, subscription_ids, user.demand_forward_tracks, 1, user, objects[0].run_id, "activate")
            if self.settings.mode == "forward_1_to_0":
                target = anchor_ns + (self.settings.deactivate_at_ms or self.settings.demand_at_ms) * 1_000_000
                if target > time.monotonic_ns():
                    await asyncio.sleep((target - time.monotonic_ns()) / 1_000_000_000)
                demand_ts = time.monotonic_ns()
                self.updates.append(Observation("demand_event", demand_ts, "local-monotonic", objects[0].run_id,
                    "p4_deterministic_synthetic_demand", user_id=user.user_id, native_operation="deterministic_demand_schedule",
                    details={"mode": self.settings.mode, "forward_tracks": list(user.deactivate_tracks)}))
                await self._update(client, subscription_ids, user.deactivate_tracks, 0, user, objects[0].run_id, "deactivate")
            end_target = (demand_ts or anchor_ns) + self.settings.observation_after_demand_ms * 1_000_000
            if end_target > time.monotonic_ns():
                await asyncio.sleep((end_target - time.monotonic_ns()) / 1_000_000_000)
            self._phase(objects[0].run_id, "MEASUREMENT_END", role="subscriber", user_id=user.user_id)
            teardown_start = self._phase(objects[0].run_id, "ENDPOINT_TEARDOWN_START", role="subscriber", user_id=user.user_id)
            client.close()
        teardown_complete = self._phase(objects[0].run_id, "ENDPOINT_TEARDOWN_COMPLETE", role="subscriber", user_id=user.user_id)
        return {"user_id": user.user_id, "protocol_negotiation_subscriber": probe.as_dict(), "started_ts_ns": started,
                "ended_ts_ns": time.monotonic_ns(), "demand_event_ts_ns": demand_ts, "expected_objects": len(expected),
                "received_objects": len(validation.seen), "missing_objects": len(expected) - len(validation.seen),
                "duplicate_objects": validation.duplicate, "malformed_objects": validation.malformed,
                "unexpected_objects": validation.unexpected, "out_of_order_objects": validation.out_of_order,
                "session_established_ts_ns": session_established, "measurement_start_ts_ns": measurement_start,
                "teardown_start_ts_ns": teardown_start, "teardown_complete_ts_ns": teardown_complete}

    async def run_subscriber(self, planned: Iterable[PlannedObject], anchor_ns: int) -> dict[str, Any]:
        objects, grouped, expected = self._objects(planned, self.settings.seed)
        users = await asyncio.gather(*(self._run_user(user, objects, grouped, expected, anchor_ns) for user in self.settings.users))
        return {"users": users, "started_ts_ns": min(item["started_ts_ns"] for item in users),
                "ended_ts_ns": max(item["ended_ts_ns"] for item in users)}
