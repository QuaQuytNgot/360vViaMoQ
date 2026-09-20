"""Native draft-18 late-join/FETCH experiment adapter."""

from __future__ import annotations

import asyncio
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .aiomoqt_d18_fetch import apply_aiomoqt_d18_fetch_patch
from .events import EventRecorder
from .models import Observation, PlannedObject
from .p1_adapter import P1AdapterError, _probe_from_session, decode_p1_identity, p1_payload
from .p2_adapter import P2AdapterSettings, _P2PublishedTrack
from .publisher import LivePublisher


@dataclass(frozen=True)
class P5AdapterSettings(P2AdapterSettings):
    mode: str = "live_only"
    demand_at_ms: int = 0
    observation_after_demand_ms: int = 2500
    joining_start: int = 0
    standalone_range: tuple[int, int, int, int] | None = None

    @classmethod
    def from_config(cls, config: Mapping[str, Any], *, namespace: str) -> "P5AdapterSettings":
        p5, workload, runtime, relay = (config.get(key) for key in ("p5", "workload", "runtime", "relay"))
        if not all(isinstance(value, Mapping) for value in (p5, workload, runtime, relay)):
            raise P1AdapterError("P5 requires p5, workload, runtime, and relay mappings")
        assert isinstance(p5, Mapping) and isinstance(workload, Mapping)
        assert isinstance(runtime, Mapping) and isinstance(relay, Mapping)
        tracks = workload.get("track_ids")
        if not isinstance(tracks, list) or not tracks or not all(isinstance(track, str) for track in tracks):
            raise P1AdapterError("P5 workload.track_ids must be a non-empty string list")
        mode = p5.get("mode")
        if mode not in {"live_only", "standalone_fetch_live", "joining_fetch", "standalone_fetch_smoke"}:
            raise P1AdapterError("P5 mode must be live_only, standalone_fetch_live, joining_fetch, or standalone_fetch_smoke")
        demand_at_ms = p5.get("demand_at_ms")
        observation_ms = p5.get("observation_after_demand_ms", 2500)
        joining_start = p5.get("joining_start", 0)
        if not isinstance(demand_at_ms, int) or demand_at_ms < 0:
            raise P1AdapterError("p5.demand_at_ms must be a non-negative integer")
        if not isinstance(observation_ms, int) or observation_ms <= 0:
            raise P1AdapterError("p5.observation_after_demand_ms must be positive")
        if not isinstance(joining_start, int) or joining_start < 0:
            raise P1AdapterError("p5.joining_start must be a non-negative integer")
        interval = workload.get("object_interval_ms")
        group_ms = workload.get("group_duration_ms")
        objects_per_group = workload.get("objects_per_group")
        if not all(isinstance(value, int) and value > 0 for value in (interval, group_ms, objects_per_group)):
            raise P1AdapterError("P5 requires positive group_duration_ms, object_interval_ms, and objects_per_group")
        if interval * objects_per_group != group_ms:
            raise P1AdapterError("P5 requires object_interval_ms * objects_per_group == group_duration_ms")
        standalone_range = None
        raw_range = p5.get("standalone_range")
        if raw_range is not None:
            if (not isinstance(raw_range, Mapping) or
                    any(not isinstance(raw_range.get(key), int)
                        for key in ("start_group", "start_object", "end_group", "end_object"))):
                raise P1AdapterError("p5.standalone_range requires four integer location fields")
            standalone_range = tuple(raw_range[key] for key in
                ("start_group", "start_object", "end_group", "end_object"))
        if mode == "standalone_fetch_smoke" and standalone_range is None:
            raise P1AdapterError("standalone_fetch_smoke requires p5.standalone_range")
        return cls(
            dict(relay), namespace, str(workload.get("seed", "")),
            str(runtime.get("subscriber_id", "subscriber_0")),
            float(runtime.get("connect_timeout_s", 15)),
            float(runtime.get("receive_timeout_s", 30)),
            {track: 128 for track in tracks}, {}, None,
            float(p5.get("update_timeout_s", 5)), mode, demand_at_ms,
            observation_ms, joining_start, standalone_range,
        )


class _P5ReceiveValidation:
    def __init__(self, expected: Mapping[tuple[str, int, int], PlannedObject], seed: str,
                 run_id: str, subscriber_events: Path, transitions: Path) -> None:
        self.expected = dict(expected)
        self.seed = seed
        self.run_id = run_id
        self.subscriber = EventRecorder(subscriber_events)
        self.transitions = EventRecorder(transitions)
        self.seen_by_source: Counter[tuple[str, int, int, str]] = Counter()
        self.seen_any: Counter[tuple[str, int, int]] = Counter()
        self.malformed = 0
        self.unexpected = 0
        self.status_objects = 0
        self.records: list[dict[str, Any]] = []
        self.unexpected_records: list[dict[str, Any]] = []

    def _receive(self, msg: Any, source: str, request_id: int | None = None,
                 group_id: int | None = None) -> None:
        received = time.monotonic_ns()
        payload = getattr(msg, "payload", None)
        wire_group = group_id if group_id is not None else getattr(msg, "group_id", None)
        wire_object = getattr(msg, "object_id", None)
        # A FETCH stream can carry an Object Status with an empty payload.
        # It is protocol metadata (for example END_OF_GROUP), not a corrupt
        # application Object and therefore has no P1 identity to validate.
        status = getattr(msg, "status", None)
        if payload == b"" and status is not None and int(status) != 0:
            self.status_objects += 1
            return
        if not isinstance(payload, bytes) or not isinstance(wire_group, int) or not isinstance(wire_object, int):
            self.malformed += 1
            return
        try:
            identity = decode_p1_identity(payload)
        except P1AdapterError:
            self.malformed += 1
            return
        key = (identity["track_id"], identity["group_id"], identity["object_id"])
        planned = self.expected.get(key)
        if (planned is None or identity["run_id"] != self.run_id or
                (wire_group, wire_object) != key[1:] or payload != p1_payload(planned, self.seed)):
            self.unexpected += 1
            self.unexpected_records.append({"track_id": identity["track_id"],
                "group_id": identity["group_id"], "object_id": identity["object_id"],
                "source": source, "received_ts_ns": received,
                "payload_bytes": len(payload), "request_id": request_id})
            return
        duplicate = self.seen_any[key] > 0
        self.seen_any[key] += 1
        self.seen_by_source[(*key, source)] += 1
        details = {"delivery_source": source, "duplicate": duplicate, "request_id": request_id,
                   "scheduled_publish_ts_ns": planned.scheduled_publish_ts_ns}
        observation = Observation(
            "object_received", received, "local-monotonic", self.run_id,
            "aiomoqt_draft18_fetch" if source == "fetch" else "aiomoqt_draft18_subscribe",
            user_id="subscriber_0", track_id=planned.track_id, tile_id=planned.tile_id,
            group_id=planned.group_id, object_id=planned.object_id,
            payload_bytes=len(payload), correlation_id=f"fetch-{request_id}" if request_id is not None else None,
            native_operation="MOQTSessionQuic.on_fetch_object" if source == "fetch" else "MOQTSessionQuic.on_object_received",
            details=details,
        )
        self.subscriber.append(observation)
        self.transitions.append(observation)
        self.records.append({"track_id": planned.track_id, "group_id": planned.group_id,
                             "object_id": planned.object_id, "source": source,
                             "received_ts_ns": received, "payload_bytes": len(payload),
                             "duplicate": duplicate, "request_id": request_id})

    def on_live(self, msg: Any, _wire_bytes: int, _wall_us: int,
                group_id: int, _subgroup_id: int | None) -> None:
        # The object carries the authoritative self-identifying Track ID.
        self._receive(msg, "live", group_id=group_id)

    def on_fetch(self, msg: Any, _wire_bytes: int, _wall_us: int, request_id: int) -> None:
        self._receive(msg, "fetch", request_id)


class Moqt18P5Adapter:
    def __init__(self, settings: P5AdapterSettings, run_dir: Path) -> None:
        self.settings = settings
        self.run_dir = run_dir
        self.publisher = LivePublisher.__new__(LivePublisher)
        self.publisher.recorder = EventRecorder(run_dir / "publisher.events.jsonl")
        self.publisher.clock_domain_id = "local-monotonic"
        self.publisher.seed = settings.seed
        self.fetch_events = EventRecorder(run_dir / "fetch_events.events.jsonl")
        self.subscribe_events = EventRecorder(run_dir / "subscribe_events.events.jsonl")
        self.phases = EventRecorder(run_dir / "run_phases.events.jsonl")

    @staticmethod
    def _objects(planned: Iterable[PlannedObject], seed: str):
        objects = list(planned)
        if not objects:
            raise P1AdapterError("P5 needs planned Objects")
        expected = {(item.track_id, item.group_id, item.object_id): item for item in objects}
        if len(expected) != len(objects):
            raise P1AdapterError("P5 manifest contains duplicate identities")
        grouped: dict[str, list[PlannedObject]] = defaultdict(list)
        for item in objects:
            p1_payload(item, seed)
            grouped[item.track_id].append(item)
        return objects, grouped, expected

    def _event(self, recorder: EventRecorder, event_type: str, run_id: str, *,
               track_id: str | None = None, correlation_id: str | None = None,
               native_operation: str, **details: Any) -> int:
        timestamp = time.monotonic_ns()
        recorder.append(Observation(event_type, timestamp, "local-monotonic", run_id,
            "p5_native_draft18", user_id="subscriber_0", track_id=track_id,
            correlation_id=correlation_id, native_operation=native_operation, details=details))
        return timestamp

    async def run_publisher(self, planned: Iterable[PlannedObject], *, completion_marker: Path) -> dict[str, Any]:
        from aiomoqt.client import MOQTClient

        objects, grouped, _expected = self._objects(planned, self.settings.seed)
        relay = self.settings.relay_evidence("publisher")
        started = time.monotonic_ns()
        async with MOQTClient(**self.settings.client_kwargs("publisher")).connect() as client:
            async with asyncio.timeout(self.settings.connect_timeout_s):
                await client.client_session_init(timeout=max(1, int(self.settings.connect_timeout_s)))
            probe = _probe_from_session(client, relay)
            if not probe.success:
                raise P1AdapterError(f"publisher protocol negotiation failed: {probe.reason}")
            tracks = [_P2PublishedTrack(client, self.settings.namespace, track, values,
                                        self.publisher, publish_forward=1)
                      for track, values in sorted(grouped.items())]
            for track in tracks:
                await track.publish()
                track.start()
            async with asyncio.timeout(self.settings.receive_timeout_s):
                await asyncio.gather(*(track.done.wait() for track in tracks))
            failures = [track.failure for track in tracks if track.failure]
            if failures:
                raise P1AdapterError(f"P5 publisher failed: {failures[0]}")
            deadline = time.monotonic() + self.settings.receive_timeout_s
            while not completion_marker.exists() and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            client.close()
        return {"protocol_negotiation_publisher": probe.as_dict(), "started_ts_ns": started,
                "ended_ts_ns": time.monotonic_ns(), "published_track_count": len(tracks),
                "published_object_count": len(objects)}

    async def run_subscriber(self, planned: Iterable[PlannedObject], anchor_ns: int) -> dict[str, Any]:
        from aiomoqt.client import MOQTClient
        from aiomoqt.types import FetchType, FilterType, GroupOrder

        apply_aiomoqt_d18_fetch_patch()
        objects, grouped, expected = self._objects(planned, self.settings.seed)
        run_id = objects[0].run_id
        validation = _P5ReceiveValidation(expected, self.settings.seed, run_id,
            self.run_dir / "subscriber.events.jsonl", self.run_dir / "object_transition.events.jsonl")
        relay = self.settings.relay_evidence("subscriber")
        started = time.monotonic_ns()
        subscribe_requests: dict[str, int] = {}
        fetch_requests: dict[str, int] = {}
        subscribe_ok_ts: dict[str, int] = {}
        fetch_ok_ts: dict[str, int] = {}
        fetch_done: dict[str, bool] = {}

        async with MOQTClient(**self.settings.client_kwargs("subscriber")).connect() as client:
            async with asyncio.timeout(self.settings.connect_timeout_s):
                await client.client_session_init(timeout=max(1, int(self.settings.connect_timeout_s)))
            probe = _probe_from_session(client, relay)
            if not probe.success:
                raise P1AdapterError(f"subscriber protocol negotiation failed: {probe.reason}")
            client.on_object_received = validation.on_live
            client.on_fetch_object = validation.on_fetch
            target = anchor_ns + self.settings.demand_at_ms * 1_000_000
            if target > time.monotonic_ns():
                await asyncio.sleep((target - time.monotonic_ns()) / 1_000_000_000)
            demand_ts = self._event(self.phases, "demand_event", run_id,
                native_operation="deterministic_source_timeline_demand", mode=self.settings.mode,
                target_ts_ns=target, actual_offset_ms=(time.monotonic_ns() - anchor_ns) / 1_000_000)

            if self.settings.mode == "standalone_fetch_smoke":
                assert self.settings.standalone_range is not None
                start_group, start_object, end_group, end_object = self.settings.standalone_range
                pending_fetch = {}
                for track in sorted(grouped):
                    sent = time.monotonic_ns()
                    message = client.fetch(namespace=self.settings.namespace, track_name=track,
                        group_order=GroupOrder.ASCENDING, start_group=start_group, start_object=start_object,
                        end_group=end_group, end_object=end_object, wait_response=False)
                    request_id = getattr(message, "request_id", None)
                    if not isinstance(request_id, int):
                        raise P1AdapterError(f"FETCH did not allocate request ID for {track}")
                    fetch_requests[track] = request_id
                    self._event(self.fetch_events, "fetch_sent", run_id, track_id=track,
                        correlation_id=f"fetch-{request_id}", native_operation="MOQTSessionQuic.fetch",
                        request_id=request_id, fetch_type="STANDALONE", start_group=start_group,
                        start_object=start_object, end_group=end_group, end_object=end_object, sent_ts_ns=sent)
                    pending_fetch[track] = asyncio.create_task(client._await_response(request_id, self.settings.update_timeout_s))
                responses = await asyncio.gather(*(pending_fetch[track] for track in sorted(pending_fetch)))
                for track, response in zip(sorted(pending_fetch), responses):
                    fetch_ok_ts[track] = self._event(self.fetch_events, "fetch_ok", run_id, track_id=track,
                        correlation_id=f"fetch-{fetch_requests[track]}", native_operation="FETCH_OK",
                        request_id=fetch_requests[track], end_group_id=getattr(response, "largest_group_id", None),
                        end_object_id=getattr(response, "largest_object_id", None))

            elif self.settings.mode in {"live_only", "standalone_fetch_live"}:
                pending = {}
                for track in sorted(grouped):
                    sent = time.monotonic_ns()
                    message = client.subscribe(namespace=self.settings.namespace, track_name=track,
                        priority=128, forward=1, filter_type=FilterType.LATEST_OBJECT,
                        wait_response=False)
                    request_id = getattr(message, "request_id", None)
                    if not isinstance(request_id, int):
                        raise P1AdapterError(f"SUBSCRIBE did not allocate request ID for {track}")
                    subscribe_requests[track] = request_id
                    self._event(self.subscribe_events, "subscribe_sent", run_id, track_id=track,
                        correlation_id=f"subscribe-{request_id}", native_operation="MOQTSessionQuic.subscribe",
                        request_id=request_id, sent_ts_ns=sent, filter_type="LATEST_OBJECT", forward=1)
                    pending[track] = asyncio.create_task(client._await_response(request_id, self.settings.update_timeout_s))
                responses = await asyncio.gather(*(pending[track] for track in sorted(pending)))
                subscribe_responses = dict(zip(sorted(pending), responses))
                for track, response in subscribe_responses.items():
                    ts = self._event(self.subscribe_events, "subscribe_ok", run_id, track_id=track,
                        correlation_id=f"subscribe-{subscribe_requests[track]}", native_operation="SUBSCRIBE_OK",
                        request_id=subscribe_requests[track], largest_group_id=getattr(response, "largest_group_id", None),
                        largest_object_id=getattr(response, "largest_object_id", None))
                    subscribe_ok_ts[track] = ts

                if self.settings.mode == "standalone_fetch_live":
                    pending_fetch = {}
                    for track in sorted(grouped):
                        response = subscribe_responses[track]
                        group_id = getattr(response, "largest_group_id", None)
                        object_id = getattr(response, "largest_object_id", None)
                        if not isinstance(group_id, int) or not isinstance(object_id, int):
                            raise P1AdapterError(f"SUBSCRIBE_OK lacked Joining Location for {track}")
                        sent = time.monotonic_ns()
                        message = client.fetch(namespace=self.settings.namespace, track_name=track,
                            group_order=GroupOrder.ASCENDING, start_group=group_id, start_object=0,
                            end_group=group_id, end_object=object_id + 1, wait_response=False)
                        request_id = getattr(message, "request_id", None)
                        if not isinstance(request_id, int):
                            raise P1AdapterError(f"FETCH did not allocate request ID for {track}")
                        fetch_requests[track] = request_id
                        self._event(self.fetch_events, "fetch_sent", run_id, track_id=track,
                            correlation_id=f"fetch-{request_id}", native_operation="MOQTSessionQuic.fetch",
                            request_id=request_id, fetch_type="STANDALONE", start_group=group_id,
                            start_object=0, end_group=group_id, end_object=object_id + 1, sent_ts_ns=sent)
                        pending_fetch[track] = asyncio.create_task(client._await_response(request_id, self.settings.update_timeout_s))
                    responses = await asyncio.gather(*(pending_fetch[track] for track in sorted(pending_fetch)))
                    for track, response in zip(sorted(pending_fetch), responses):
                        fetch_ok_ts[track] = self._event(self.fetch_events, "fetch_ok", run_id, track_id=track,
                            correlation_id=f"fetch-{fetch_requests[track]}", native_operation="FETCH_OK",
                            request_id=fetch_requests[track], end_group_id=getattr(response, "largest_group_id", None),
                            end_object_id=getattr(response, "largest_object_id", None))

            else:  # native Joining Fetch
                pairs = {}
                for track in sorted(grouped):
                    sent = time.monotonic_ns()
                    sub_msg, fetch_msg = await client.join(namespace=self.settings.namespace, track_name=track,
                        subscriber_priority=128, group_order=GroupOrder.ASCENDING,
                        fetch_type=FetchType.RELATIVE_JOINING, joining_start=self.settings.joining_start,
                        wait_response=False)
                    subscribe_requests[track] = sub_msg.request_id
                    fetch_requests[track] = fetch_msg.request_id
                    self._event(self.subscribe_events, "subscribe_sent", run_id, track_id=track,
                        correlation_id=f"subscribe-{sub_msg.request_id}", native_operation="MOQTSessionQuic.join/SUBSCRIBE",
                        request_id=sub_msg.request_id, sent_ts_ns=sent, filter_type="LATEST_OBJECT", forward=1)
                    self._event(self.fetch_events, "fetch_sent", run_id, track_id=track,
                        correlation_id=f"fetch-{fetch_msg.request_id}", native_operation="MOQTSessionQuic.join/FETCH",
                        request_id=fetch_msg.request_id, fetch_type="RELATIVE_JOINING",
                        joining_request_id=sub_msg.request_id, joining_start=self.settings.joining_start,
                        sent_ts_ns=sent)
                    pairs[track] = (asyncio.create_task(client._await_response(sub_msg.request_id, self.settings.update_timeout_s)),
                                    asyncio.create_task(client._await_response(fetch_msg.request_id, self.settings.update_timeout_s)))
                for track in sorted(pairs):
                    sub_response, fetch_response = await asyncio.gather(*pairs[track])
                    subscribe_ok_ts[track] = self._event(self.subscribe_events, "subscribe_ok", run_id, track_id=track,
                        correlation_id=f"subscribe-{subscribe_requests[track]}", native_operation="SUBSCRIBE_OK",
                        request_id=subscribe_requests[track], largest_group_id=getattr(sub_response, "largest_group_id", None),
                        largest_object_id=getattr(sub_response, "largest_object_id", None))
                    fetch_ok_ts[track] = self._event(self.fetch_events, "fetch_ok", run_id, track_id=track,
                        correlation_id=f"fetch-{fetch_requests[track]}", native_operation="FETCH_OK",
                        request_id=fetch_requests[track], end_group_id=getattr(fetch_response, "largest_group_id", None),
                        end_object_id=getattr(fetch_response, "largest_object_id", None))

            end_target = demand_ts + self.settings.observation_after_demand_ms * 1_000_000
            if end_target > time.monotonic_ns():
                await asyncio.sleep((end_target - time.monotonic_ns()) / 1_000_000_000)
            for track, request_id in fetch_requests.items():
                fetch_done[track] = await client.await_fetch_done(request_id, timeout=1.0)
                self._event(self.fetch_events, "fetch_stream_fin" if fetch_done[track] else "fetch_stream_incomplete",
                    run_id, track_id=track, correlation_id=f"fetch-{request_id}",
                    native_operation="await_fetch_done", request_id=request_id)
            measurement_end = self._event(self.phases, "measurement_end", run_id,
                native_operation="p5_measurement_window")
            self._event(self.phases, "run_phase", run_id,
                native_operation="ENDPOINT_TEARDOWN_START",
                phase="ENDPOINT_TEARDOWN_START", role="subscriber")
            client.close()

        self._event(self.phases, "run_phase", run_id,
            native_operation="ENDPOINT_TEARDOWN_COMPLETE",
            phase="ENDPOINT_TEARDOWN_COMPLETE", role="subscriber")
        return {"protocol_negotiation_subscriber": probe.as_dict(), "started_ts_ns": started,
                "ended_ts_ns": time.monotonic_ns(), "demand_event_ts_ns": demand_ts,
                "measurement_end_ts_ns": measurement_end, "subscribe_request_ids": subscribe_requests,
                "fetch_request_ids": fetch_requests, "subscribe_ok_ts_ns": subscribe_ok_ts,
                "fetch_ok_ts_ns": fetch_ok_ts, "fetch_done": fetch_done,
                "received_records": validation.records, "malformed_objects": validation.malformed,
                "unexpected_objects": validation.unexpected,
                "unexpected_records": validation.unexpected_records,
                "status_objects": validation.status_objects}
