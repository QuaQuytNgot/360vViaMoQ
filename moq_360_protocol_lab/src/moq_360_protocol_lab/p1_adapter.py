"""Native draft-18 P1 publisher/subscriber adapter.

This is deliberately a small application adapter, not a second MOQT codec.
It uses aiomoqt's public client, Track, subgroup-header, and stream APIs and
therefore sends its Objects only over the configured relay connection.  Local
objects are used solely as the deterministic expected-data manifest.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from .backends import ProtocolProbe
from .events import EventRecorder
from .models import Observation, PlannedObject
from .publisher import LivePublisher
from .subscriber import SubscriberRecorder


class P1AdapterError(RuntimeError):
    """The native P1 path could not complete or violated its invariants."""


def p1_payload(item: PlannedObject, seed: str) -> bytes:
    """Return a self-identifying, fixed-length deterministic Object payload.

    The prefix is intentionally application metadata rather than a MOQT
    extension: a relay need not understand it for the subscriber to prove the
    identity and bytes of each delivered Object.
    """
    identity = {
        "format": "moq-360-p1-v1",
        "run_id": item.run_id,
        "track_id": item.track_id,
        "group_id": item.group_id,
        "object_id": item.object_id,
        "scheduled_publish_ts_ns": item.scheduled_publish_ts_ns,
        "payload_sequence": item.group_id * 1_000_000 + item.object_id,
    }
    prefix = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    if len(prefix) > item.payload_bytes:
        raise P1AdapterError(
            f"Object {item.track_id}/{item.group_id}/{item.object_id} has {item.payload_bytes} bytes, "
            f"but P1 identity metadata needs {len(prefix)} bytes"
        )
    return prefix + hashlib.shake_256(seed.encode("utf-8") + b"|" + prefix).digest(item.payload_bytes - len(prefix))


def decode_p1_identity(payload: bytes) -> dict[str, Any]:
    """Parse only the compact P1 identity prefix; malformed input is rejected."""
    try:
        raw, _padding = payload.split(b"\n", 1)
        identity = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise P1AdapterError("payload does not contain a valid P1 identity prefix") from exc
    required = ("format", "run_id", "track_id", "group_id", "object_id", "scheduled_publish_ts_ns", "payload_sequence")
    if not isinstance(identity, dict) or any(key not in identity for key in required):
        raise P1AdapterError("payload identity is missing a required field")
    if identity["format"] != "moq-360-p1-v1":
        raise P1AdapterError("payload has an unexpected identity format")
    if not isinstance(identity["track_id"], str) or not isinstance(identity["run_id"], str):
        raise P1AdapterError("payload identity has invalid text fields")
    if any(not isinstance(identity[key], int) for key in ("group_id", "object_id", "scheduled_publish_ts_ns", "payload_sequence")):
        raise P1AdapterError("payload identity has invalid numeric fields")
    return identity


@dataclass(frozen=True)
class P1AdapterSettings:
    relay: Mapping[str, Any]
    namespace: str
    seed: str
    user_id: str
    connect_timeout_s: float
    receive_timeout_s: float
    publisher_priority: int = 128
    subscriber_priority: int = 128

    @classmethod
    def from_config(cls, config: Mapping[str, Any], *, namespace: str) -> "P1AdapterSettings":
        relay = config.get("relay")
        workload = config.get("workload")
        runtime = config.get("runtime")
        if not isinstance(relay, Mapping) or not isinstance(workload, Mapping) or not isinstance(runtime, Mapping):
            raise P1AdapterError("live P1 requires relay, workload, and runtime mappings")
        seed = workload.get("seed")
        if not isinstance(seed, str) or not seed:
            raise P1AdapterError("workload.seed is required for a native P1 run")
        user_id = runtime.get("subscriber_id", "subscriber_0")
        if not isinstance(user_id, str) or not user_id:
            raise P1AdapterError("runtime.subscriber_id must be a non-empty string")
        connect_timeout_s = runtime.get("connect_timeout_s", 15)
        receive_timeout_s = runtime.get("receive_timeout_s", 30)
        if not isinstance(connect_timeout_s, (int, float)) or connect_timeout_s <= 0:
            raise P1AdapterError("runtime.connect_timeout_s must be positive")
        if not isinstance(receive_timeout_s, (int, float)) or receive_timeout_s <= 0:
            raise P1AdapterError("runtime.receive_timeout_s must be positive")
        return cls(dict(relay), namespace, seed, user_id, float(connect_timeout_s), float(receive_timeout_s))


def _probe_from_session(session: Any, relay: Mapping[str, Any]) -> ProtocolProbe:
    quic = getattr(session, "_quic", None)
    alpn_reader = getattr(quic, "_negotiated_alpn", None)
    alpn = alpn_reader(None) if callable(alpn_reader) else None
    draft = getattr(session, "negotiated_draft", None)
    success = draft == 18 and alpn == "moqt-18"
    return ProtocolProbe(
        requested_draft=18,
        negotiated_draft=draft if isinstance(draft, int) else None,
        transport="raw_quic",
        alpn=alpn if isinstance(alpn, str) else None,
        relay={key: relay.get(key) for key in ("implementation", "version", "commit", "address", "port", "path")},
        success=success,
        reason=None if success else f"required draft/alpn is 18/moqt-18; observed {draft!r}/{alpn!r}",
        backend="moqt18",
    )


class _P1PublishedTrack:
    """One published Track; dispatch is owned once by the containing session."""

    def __init__(self, session: Any, namespace: str, track_id: str, objects: list[PlannedObject], sender: LivePublisher):
        self.session = session
        self.namespace = namespace
        self.track_id = track_id
        self.objects = sorted(objects, key=lambda item: (item.group_id, item.object_id))
        self.sender = sender
        self.done = asyncio.Event()
        self.failure: BaseException | None = None
        self.request_id: int | None = None
        self.track_alias: int | None = None
        self.generation_task: asyncio.Task[None] | None = None

    async def publish(self) -> None:
        message = self.session.publish(
            namespace=self.namespace, track_name=self.track_id, forward=0,
        )
        self.request_id = getattr(message, "request_id", None)
        self.track_alias = getattr(message, "track_alias", None)
        if not isinstance(self.request_id, int) or not isinstance(self.track_alias, int):
            raise P1AdapterError(f"PUBLISH did not allocate request/track alias for {self.track_id}")

    def start(self) -> None:
        if self.generation_task is None:
            assert self.track_alias is not None
            self.generation_task = asyncio.create_task(self._generate(self.session, self.track_alias))

    async def _generate(self, session: Any, track_alias: int) -> None:
        try:
            grouped: dict[int, list[PlannedObject]] = defaultdict(list)
            for item in self.objects:
                grouped[item.group_id].append(item)
            for group_id, group in sorted(grouped.items()):
                scheduled = group[0].scheduled_publish_ts_ns
                delay_s = (scheduled - time.monotonic_ns()) / 1_000_000_000
                if delay_s > 0:
                    await asyncio.sleep(delay_s)
                stream_id = await session.open_uni_stream()
                header = session.subgroup_header(
                    track_alias=track_alias,
                    group_id=group_id,
                    subgroup_id=0,
                    publisher_priority=128,
                    extensions_present=False,
                )
                await session.stream_write_drain(stream_id, header.serialize().data)
                for item in group:
                    actual_ts_ns = time.monotonic_ns()
                    payload = p1_payload(item, self.sender.seed)
                    wire = header.next_object_bytes(payload=payload, object_id=item.object_id)
                    await session.stream_write_drain(stream_id, wire)
                    self.sender.recorder.append(Observation(
                        event_type="published", observed_ts_ns=actual_ts_ns,
                        clock_domain_id=self.sender.clock_domain_id, run_id=item.run_id,
                        track_id=item.track_id, tile_id=item.tile_id, group_id=item.group_id,
                        object_id=item.object_id, payload_bytes=len(payload),
                        provenance="aiomoqt_draft18_subgroup_object",
                        native_operation="MOQTSessionQuic.subgroup_header/stream_write_drain",
                        details={"scheduled_publish_ts_ns": item.scheduled_publish_ts_ns,
                                 "pacer_lateness_ns": max(0, actual_ts_ns - item.scheduled_publish_ts_ns)},
                    ))
                terminal_id = group[-1].object_id + 1
                session.stream_write(stream_id, header.end_group(object_id=terminal_id).data, end_stream=True)
        except BaseException as exc:
            self.failure = exc
            raise
        finally:
            self.done.set()


@dataclass
class P1ReceiveValidation:
    expected: dict[tuple[str, int, int], PlannedObject]
    seed: str
    recorder: SubscriberRecorder
    seen: set[tuple[str, int, int]] = field(default_factory=set)
    last_location: dict[str, tuple[int, int]] = field(default_factory=dict)
    malformed: int = 0
    unexpected: int = 0
    duplicate: int = 0
    out_of_order: int = 0

    def on_object(self, msg: Any, _wire_bytes: int, _wall_us: int, group_id: int, _subgroup_id: int | None) -> None:
        received_ts_ns = time.monotonic_ns()
        payload = getattr(msg, "payload", None)
        object_id = getattr(msg, "object_id", None)
        if not isinstance(payload, bytes) or not isinstance(group_id, int) or not isinstance(object_id, int):
            self.malformed += 1
            return
        try:
            identity = decode_p1_identity(payload)
        except P1AdapterError:
            self.malformed += 1
            return
        key = (identity["track_id"], identity["group_id"], identity["object_id"])
        planned = self.expected.get(key)
        if (planned is None or identity["run_id"] != (planned.run_id if planned else None)
                or group_id != identity["group_id"] or object_id != identity["object_id"]):
            self.unexpected += 1
            return
        if payload != p1_payload(planned, self.seed):
            self.malformed += 1
            return
        if key in self.seen:
            self.duplicate += 1
            self.recorder.recorder.append(Observation(
                event_type="duplicate", observed_ts_ns=received_ts_ns, clock_domain_id=self.recorder.clock_domain_id,
                run_id=planned.run_id, user_id=self.recorder.user_id, track_id=planned.track_id, tile_id=planned.tile_id,
                group_id=planned.group_id, object_id=planned.object_id, payload_bytes=len(payload),
                provenance="aiomoqt_draft18_receive", native_operation="MOQTSessionQuic.on_object_received",
            ))
            return
        location = (planned.group_id, planned.object_id)
        previous = self.last_location.get(planned.track_id)
        if previous is not None and location < previous:
            self.out_of_order += 1
        self.last_location[planned.track_id] = max(previous or location, location)
        self.seen.add(key)
        self.recorder.first_received(planned, len(payload), native_operation="MOQTSessionQuic.on_object_received")
        self.recorder.completed(planned, len(payload), native_operation="MOQTSessionQuic.on_object_received")


class Moqt18P1Adapter:
    """Runs one independent publisher and subscriber through one real relay."""

    def __init__(self, settings: P1AdapterSettings, publisher_events: Path, subscriber_events: Path):
        self.settings = settings
        self.publisher = LivePublisher.__new__(LivePublisher)
        # LivePublisher's constructor needs a pacer that this native sender
        # intentionally does not use; its recorder/seed attributes are enough.
        self.publisher.recorder = EventRecorder(publisher_events)
        self.publisher.clock_domain_id = "local-monotonic"
        self.publisher.seed = settings.seed
        self.subscriber = SubscriberRecorder(settings.user_id, EventRecorder(subscriber_events), "local-monotonic")

    async def run(self, planned: Iterable[PlannedObject]) -> dict[str, Any]:
        from aiomoqt.client import MOQTClient
        from aiomoqt.messages import RequestUpdate
        from aiomoqt.types import FilterType, MOQTMessageType, ParamType

        objects = list(planned)
        if not objects:
            raise P1AdapterError("P1 adapter requires planned Objects")
        for item in objects:
            if "|" in item.track_id or "\n" in item.track_id:
                raise P1AdapterError("P1 track IDs cannot contain pipe or newline")
            p1_payload(item, self.settings.seed)  # fail before traffic if metadata cannot fit
        expected = {(item.track_id, item.group_id, item.object_id): item for item in objects}
        if len(expected) != len(objects):
            raise P1AdapterError("P1 manifest has duplicate Track/Group/Object identities")
        relay = self.settings.relay
        kwargs = dict(host=relay["address"], port=relay["port"], path=relay.get("path", ""), use_quic=True,
                      verify_tls=relay.get("verify_tls", True), supported_drafts=18)
        publisher_client = MOQTClient(**kwargs)
        subscriber_client = MOQTClient(**kwargs)
        by_track: dict[str, list[PlannedObject]] = defaultdict(list)
        for item in objects:
            by_track[item.track_id].append(item)
        validation = P1ReceiveValidation(expected, self.settings.seed, self.subscriber)
        started_ns = time.monotonic_ns()
        async with publisher_client.connect() as publisher_session:
            async with asyncio.timeout(self.settings.connect_timeout_s):
                await publisher_session.client_session_init(timeout=max(1, int(self.settings.connect_timeout_s)))
            publisher_probe = _probe_from_session(publisher_session, relay)
            if not publisher_probe.success:
                raise P1AdapterError(f"publisher protocol negotiation failed: {publisher_probe.reason}")
            tracks = [_P1PublishedTrack(publisher_session, self.settings.namespace, track_id, track_objects, self.publisher)
                      for track_id, track_objects in sorted(by_track.items())]
            tracks_by_request: dict[int, _P1PublishedTrack] = {}

            async def on_relay_update(_session: Any, message: Any) -> None:
                # The relay's initial Forward signal is required for its
                # normal PUBLISH/SUBSCRIBE flow.  P1 never sends a dynamic
                # REQUEST_UPDATE; it only reacts to the relay's one-time
                # forwarding request and keeps all priorities equal.
                if not isinstance(message, RequestUpdate):
                    return
                track = tracks_by_request.get(getattr(message, "request_id", None))
                forward = message.parameters.get(ParamType.FORWARD) if message.parameters else None
                if track is not None and forward:
                    track.start()

            publisher_session.register_handler(MOQTMessageType.SUBSCRIBE_UPDATE, on_relay_update)
            for track in tracks:
                await track.publish()
                assert track.request_id is not None
                tracks_by_request[track.request_id] = track
            async with subscriber_client.connect() as subscriber_session:
                async with asyncio.timeout(self.settings.connect_timeout_s):
                    await subscriber_session.client_session_init(timeout=max(1, int(self.settings.connect_timeout_s)))
                subscriber_probe = _probe_from_session(subscriber_session, relay)
                if not subscriber_probe.success:
                    raise P1AdapterError(f"subscriber protocol negotiation failed: {subscriber_probe.reason}")
                subscriber_session.on_object_received = validation.on_object
                for track_id in sorted(by_track):
                    await subscriber_session.subscribe(
                        namespace=self.settings.namespace, track_name=track_id,
                        priority=self.settings.subscriber_priority, forward=1,
                        filter_type=FilterType.LATEST_OBJECT, wait_response=True,
                    )
                try:
                    async with asyncio.timeout(self.settings.receive_timeout_s):
                        while len(validation.seen) < len(expected):
                            await asyncio.sleep(0.01)
                except TimeoutError:
                    pass
                try:
                    async with asyncio.timeout(self.settings.receive_timeout_s):
                        await asyncio.gather(*(track.done.wait() for track in tracks))
                except TimeoutError as exc:
                    waiting = [track.track_id for track in tracks if not track.done.is_set()]
                    raise P1AdapterError(f"publisher tracks did not receive relay forwarding: {waiting}") from exc
                failures = [track.failure for track in tracks if track.failure is not None]
                if failures:
                    raise P1AdapterError(f"publisher generation failed: {type(failures[0]).__name__}: {failures[0]}")
                # aiomoqt 0.10.6 exposes a synchronous close() method that
                # schedules the QUIC close; it is not an awaitable.
                subscriber_session.close()
            publisher_session.close()
        end_ns = time.monotonic_ns()
        missing = len(expected) - len(validation.seen)
        return {
            "protocol_negotiation_publisher": publisher_probe.as_dict(),
            "protocol_negotiation_subscriber": subscriber_probe.as_dict(),
            "expected_objects": len(expected), "received_objects": len(validation.seen), "missing_objects": missing,
            "duplicate_objects": validation.duplicate, "malformed_objects": validation.malformed,
            "unexpected_objects": validation.unexpected, "out_of_order_objects": validation.out_of_order,
            "started_ts_ns": started_ns, "ended_ts_ns": end_ns,
            "integrity_passed": missing == 0 and validation.duplicate == 0 and validation.malformed == 0 and validation.unexpected == 0,
        }
