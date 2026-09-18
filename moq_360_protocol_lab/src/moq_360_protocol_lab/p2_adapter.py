"""Native P2 sender/receiver path over the qualified draft-18 relay.

The adapter deliberately reuses P1 identity validation and only adds two P2
concerns: finer object pacing and a conformant subscriber-priority update on
an established subscription stream.  Receiver observations are explicitly a
data-plane proxy, not scheduler instrumentation.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .aiomoqt_d18_update import RequestUpdateSent, send_subscriber_priority_update, wait_for_update_response
from .events import EventRecorder
from .models import Observation, PlannedObject
from .p1_adapter import P1AdapterError, P1ReceiveValidation, _probe_from_session, p1_payload
from .publisher import LivePublisher
from .subscriber import SubscriberRecorder


@dataclass(frozen=True)
class P2AdapterSettings:
    relay: Mapping[str, Any]
    namespace: str
    seed: str
    user_id: str
    connect_timeout_s: float
    receive_timeout_s: float
    initial_priorities: Mapping[str, int]
    updated_priorities: Mapping[str, int]
    switch_at_ms: int | None
    update_timeout_s: float

    @classmethod
    def from_config(cls, config: Mapping[str, Any], *, namespace: str) -> "P2AdapterSettings":
        relay, workload, runtime, p2 = (config.get(name) for name in ("relay", "workload", "runtime", "p2"))
        if not all(isinstance(value, Mapping) for value in (relay, workload, runtime, p2)):
            raise P1AdapterError("P2 requires relay, workload, runtime, and p2 mappings")
        assert isinstance(relay, Mapping) and isinstance(workload, Mapping) and isinstance(runtime, Mapping) and isinstance(p2, Mapping)
        tracks = workload.get("track_ids")
        initial, updated = p2.get("initial_priorities"), p2.get("updated_priorities", {})
        if not isinstance(tracks, list) or not isinstance(initial, Mapping) or not isinstance(updated, Mapping):
            raise P1AdapterError("P2 requires track_ids, initial_priorities, and updated_priorities mappings")
        def priorities(value: Mapping[str, Any], name: str) -> dict[str, int]:
            result = {str(key): item for key, item in value.items()}
            if set(result) - set(tracks) or any(not isinstance(item, int) or not 0 <= item <= 255 for item in result.values()):
                raise P1AdapterError(f"{name} must contain only track IDs with uint8 values")
            return result
        initial_result, updated_result = priorities(initial, "p2.initial_priorities"), priorities(updated, "p2.updated_priorities")
        if set(initial_result) != set(tracks):
            raise P1AdapterError("p2.initial_priorities must specify every track")
        switch_at_ms = p2.get("switch_at_ms")
        if switch_at_ms is not None and (not isinstance(switch_at_ms, int) or switch_at_ms < 0 or not updated_result):
            raise P1AdapterError("p2.switch_at_ms requires non-negative int and non-empty updated_priorities")
        return cls(
            dict(relay), namespace, str(workload.get("seed", "")), str(runtime.get("subscriber_id", "subscriber_0")),
            float(runtime.get("connect_timeout_s", 15)), float(runtime.get("receive_timeout_s", 45)),
            initial_result, updated_result, switch_at_ms, float(p2.get("update_timeout_s", 5)),
        )

    def client_kwargs(self, role: str) -> dict[str, Any]:
        """Return the role-specific relay address for an isolated topology.

        A relay namespace has one address facing each endpoint namespace.  A
        single ``relay.address`` remains accepted for a non-isolated local
        smoke, but P2's namespace configurations supply both explicit
        addresses so neither endpoint can accidentally originate on the wrong
        side of the shaped link.
        """
        if role not in {"publisher", "subscriber"}:
            raise ValueError("role must be publisher or subscriber")
        address = self.relay.get(f"{role}_address", self.relay.get("address"))
        if not isinstance(address, str) or not address:
            raise P1AdapterError(f"relay.{role}_address or relay.address is required")
        return dict(host=address, port=self.relay["port"], path=self.relay.get("path", ""), use_quic=True,
                    verify_tls=self.relay.get("verify_tls", True), supported_drafts=18)

    def relay_evidence(self, role: str) -> dict[str, Any]:
        result = dict(self.relay)
        result["address"] = self.client_kwargs(role)["host"]
        return result


class _P2PublishedTrack:
    def __init__(self, session: Any, namespace: str, track_id: str, objects: list[PlannedObject], publisher: LivePublisher,
                 *, publish_forward: int = 0):
        self.session, self.namespace, self.track_id, self.objects, self.publisher = session, namespace, track_id, sorted(objects, key=lambda item: item.scheduled_publish_ts_ns), publisher
        if publish_forward not in (0, 1):
            raise ValueError("publish_forward must be 0 or 1")
        self.publish_forward = publish_forward
        self.request_id: int | None = None
        self.track_alias: int | None = None
        self.task: asyncio.Task[None] | None = None
        self.done, self.failure = asyncio.Event(), None

    async def publish(self) -> None:
        message = self.session.publish(namespace=self.namespace, track_name=self.track_id, forward=self.publish_forward)
        self.request_id, self.track_alias = getattr(message, "request_id", None), getattr(message, "track_alias", None)
        if not isinstance(self.request_id, int) or not isinstance(self.track_alias, int):
            raise P1AdapterError(f"PUBLISH did not allocate request state for {self.track_id}")

    def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(self._generate())

    async def _generate(self) -> None:
        try:
            assert self.track_alias is not None
            last_by_group: dict[int, int] = defaultdict(int)
            for item in self.objects:
                last_by_group[item.group_id] = max(last_by_group[item.group_id], item.object_id)
            for item in self.objects:
                delay_s = (item.scheduled_publish_ts_ns - time.monotonic_ns()) / 1_000_000_000
                if delay_s > 0:
                    await asyncio.sleep(delay_s)
                stream_id = await self.session.open_uni_stream()
                header = self.session.subgroup_header(track_alias=self.track_alias, group_id=item.group_id,
                    subgroup_id=item.object_id, publisher_priority=128, extensions_present=False)
                payload = p1_payload(item, self.publisher.seed)
                wire = header.serialize().data + header.next_object_bytes(payload=payload, object_id=item.object_id)
                if item.object_id == last_by_group[item.group_id]:
                    wire += header.end_group(object_id=item.object_id + 1).data
                self.session.stream_write(stream_id, wire, end_stream=True)
                actual = time.monotonic_ns()
                self.publisher.recorder.append(Observation(
                    event_type="published", observed_ts_ns=actual, clock_domain_id=self.publisher.clock_domain_id,
                    run_id=item.run_id, track_id=item.track_id, tile_id=item.tile_id, group_id=item.group_id,
                    object_id=item.object_id, payload_bytes=len(payload), provenance="aiomoqt_draft18_subgroup_object",
                    native_operation="MOQTSessionQuic.subgroup_header/stream_write",
                    details={"scheduled_publish_ts_ns": item.scheduled_publish_ts_ns,
                             "pacer_lateness_ns": max(0, actual - item.scheduled_publish_ts_ns)}))
        except BaseException as exc:
            self.failure = exc
            raise
        finally:
            self.done.set()


class Moqt18P2Adapter:
    def __init__(self, settings: P2AdapterSettings, publisher_events: Path, subscriber_events: Path, update_events: Path):
        self.settings = settings
        self.publisher = LivePublisher.__new__(LivePublisher)
        self.publisher.recorder, self.publisher.clock_domain_id, self.publisher.seed = EventRecorder(publisher_events), "local-monotonic", settings.seed
        self.subscriber = SubscriberRecorder(settings.user_id, EventRecorder(subscriber_events), "local-monotonic")
        self.updates = EventRecorder(update_events)

    @staticmethod
    def _objects(planned: Iterable[PlannedObject], seed: str) -> tuple[list[PlannedObject], dict[str, list[PlannedObject]], dict[tuple[str, int, int], PlannedObject]]:
        objects = list(planned)
        if not objects:
            raise P1AdapterError("P2 needs planned objects")
        expected = {(item.track_id, item.group_id, item.object_id): item for item in objects}
        if len(expected) != len(objects):
            raise P1AdapterError("P2 manifest has duplicate identities")
        grouped: dict[str, list[PlannedObject]] = defaultdict(list)
        for item in objects:
            p1_payload(item, seed)  # reject undersized identity payloads before traffic
            grouped[item.track_id].append(item)
        return objects, grouped, expected

    async def run_publisher(self, planned: Iterable[PlannedObject], *, completion_marker: Path | None = None) -> dict[str, Any]:
        """Publish only; intended to execute inside ``moq-p2-pub``.

        It waits for the relay's forwarding signal rather than producing data
        locally before a real subscription has crossed the relay.
        """
        from aiomoqt.client import MOQTClient
        from aiomoqt.messages import RequestUpdate
        from aiomoqt.types import FilterType, MOQTMessageType, ParamType

        objects, grouped, _expected = self._objects(planned, self.settings.seed)
        relay = self.settings.relay_evidence("publisher")
        started = time.monotonic_ns()
        async with MOQTClient(**self.settings.client_kwargs("publisher")).connect() as pub_client:
            async with asyncio.timeout(self.settings.connect_timeout_s):
                await pub_client.client_session_init(timeout=max(1, int(self.settings.connect_timeout_s)))
            pub_probe = _probe_from_session(pub_client, relay)
            if not pub_probe.success:
                raise P1AdapterError(f"publisher protocol negotiation failed: {pub_probe.reason}")
            tracks = [_P2PublishedTrack(pub_client, self.settings.namespace, track_id, values, self.publisher)
                      for track_id, values in sorted(grouped.items())]
            request_tracks: dict[int, _P2PublishedTrack] = {}
            async def relay_update(_session: Any, message: Any) -> None:
                if isinstance(message, RequestUpdate) and message.parameters and message.parameters.get(ParamType.FORWARD):
                    track = request_tracks.get(getattr(message, "request_id", None))
                    if track: track.start()
            pub_client.register_handler(MOQTMessageType.SUBSCRIBE_UPDATE, relay_update)
            for track in tracks:
                await track.publish(); assert track.request_id is not None; request_tracks[track.request_id] = track
            try:
                async with asyncio.timeout(self.settings.receive_timeout_s):
                    await asyncio.gather(*(track.done.wait() for track in tracks))
            except TimeoutError as exc:
                waiting = [track.track_id for track in tracks if not track.done.is_set()]
                raise P1AdapterError(f"P2 publishers did not receive relay forwarding: {waiting}") from exc
            failures = [track.failure for track in tracks if track.failure]
            if failures: raise P1AdapterError(f"P2 publisher failed: {failures[0]}")
            # Do not close the publisher's QUIC session while the relay still
            # owns downstream backlog.  The marker is lifecycle coordination
            # only (the Object data path remains exclusively MOQT via relay).
            if completion_marker is not None:
                deadline = time.monotonic() + self.settings.receive_timeout_s
                while not completion_marker.exists() and time.monotonic() < deadline:
                    await asyncio.sleep(0.05)
            pub_client.close()
        ended = time.monotonic_ns()
        return {"protocol_negotiation_publisher": pub_probe.as_dict(), "started_ts_ns": started,
                "ended_ts_ns": ended, "published_track_count": len(tracks), "published_object_count": len(objects)}

    async def run_subscriber(self, planned: Iterable[PlannedObject], switch_anchor_ns: int) -> dict[str, Any]:
        """Subscribe and receive only; intended for ``moq-p2-sub``."""
        from aiomoqt.client import MOQTClient
        from aiomoqt.types import FilterType

        objects, grouped, expected = self._objects(planned, self.settings.seed)
        relay = self.settings.relay_evidence("subscriber")
        validation = P1ReceiveValidation(expected, self.settings.seed, self.subscriber)
        started = time.monotonic_ns()
        async with MOQTClient(**self.settings.client_kwargs("subscriber")).connect() as sub_client:
            async with asyncio.timeout(self.settings.connect_timeout_s):
                await sub_client.client_session_init(timeout=max(1, int(self.settings.connect_timeout_s)))
            sub_probe = _probe_from_session(sub_client, relay)
            if not sub_probe.success:
                raise P1AdapterError(f"subscriber protocol negotiation failed: {sub_probe.reason}")
            sub_client.on_object_received = validation.on_object
            subscription_ids: dict[str, int] = {}
            for track_id in sorted(grouped):
                response = await sub_client.subscribe(namespace=self.settings.namespace, track_name=track_id,
                    priority=self.settings.initial_priorities[track_id], forward=1,
                    filter_type=FilterType.LATEST_OBJECT, wait_response=True)
                request_id = getattr(response, "request_id", None)
                if not isinstance(request_id, int):
                    raise P1AdapterError(f"SUBSCRIBE response lacked request ID for {track_id}")
                subscription_ids[track_id] = request_id
            if self.settings.switch_at_ms is not None:
                delay_s = (switch_anchor_ns + self.settings.switch_at_ms * 1_000_000 - time.monotonic_ns()) / 1_000_000_000
                if delay_s > 0: await asyncio.sleep(delay_s)
                sent: list[tuple[str, RequestUpdateSent]] = []
                for track_id, priority in sorted(self.settings.updated_priorities.items()):
                    generated = time.monotonic_ns()
                    update = send_subscriber_priority_update(sub_client, subscription_ids[track_id], priority)
                    sent.append((track_id, update))
                    self.updates.append(Observation(event_type="request_update_sent", observed_ts_ns=update.sent_ts_ns,
                        clock_domain_id="local-monotonic", run_id=objects[0].run_id, track_id=track_id,
                        correlation_id=f"update-{update.request_id}", provenance="aiomoqt_draft18_request_update",
                        native_operation="send_stream_message", details={"generated_ts_ns": generated,
                        "encode_ts_ns": update.encode_ts_ns, "request_id": update.request_id,
                        "subscription_request_id": update.subscription_request_id, "subscriber_priority": priority}))
                for track_id, update in sent:
                    try:
                        _response, received = await wait_for_update_response(sub_client, update, timeout_s=self.settings.update_timeout_s)
                        event, details = "request_ok", {"request_id": update.request_id, "control_response_latency_ns": received - update.sent_ts_ns}
                    except Exception as exc:
                        received, event, details = time.monotonic_ns(), "request_error", {"request_id": update.request_id, "reason": str(exc)}
                    self.updates.append(Observation(event_type=event, observed_ts_ns=received, clock_domain_id="local-monotonic",
                        run_id=objects[0].run_id, track_id=track_id, correlation_id=f"update-{update.request_id}",
                        provenance="aiomoqt_draft18_request_update", native_operation="REQUEST_OK/REQUEST_ERROR", details=details))
            try:
                async with asyncio.timeout(self.settings.receive_timeout_s):
                    while len(validation.seen) < len(expected): await asyncio.sleep(0.01)
            except TimeoutError:
                pass
            sub_client.close()
        ended = time.monotonic_ns()
        return {"protocol_negotiation_subscriber": sub_probe.as_dict(), "started_ts_ns": started, "ended_ts_ns": ended,
                "expected_objects": len(expected), "received_objects": len(validation.seen),
                "missing_objects": len(expected) - len(validation.seen), "duplicate_objects": validation.duplicate,
                "malformed_objects": validation.malformed, "unexpected_objects": validation.unexpected,
                "out_of_order_objects": validation.out_of_order,
                "integrity_passed": len(validation.seen) == len(expected) and not any((validation.duplicate, validation.malformed, validation.unexpected))}
