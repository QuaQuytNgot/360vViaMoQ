"""Native P3A Object Delivery Timeout adapter over the qualified relay.

P3A deliberately reuses P2's real publisher and namespace topology.  Its
only protocol change is the d18 Object Delivery Timeout subscriber parameter;
the relay's native timer is the sole expiry mechanism.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .aiomoqt_d18_timeout import DELIVERY_TIMEOUT_ERROR, install_stream_reset_observer, object_delivery_timeout_parameters
from .models import Observation, PlannedObject
from .p1_adapter import P1AdapterError, P1ReceiveValidation, _probe_from_session
from .p2_adapter import Moqt18P2Adapter, P2AdapterSettings


@dataclass(frozen=True)
class P3AdapterSettings(P2AdapterSettings):
    object_delivery_timeout_ms: int | None = None
    treatment: str = "control_no_timeout"

    @classmethod
    def from_config(cls, config: Mapping[str, Any], *, namespace: str) -> "P3AdapterSettings":
        p3 = config.get("p3")
        if not isinstance(p3, Mapping):
            raise P1AdapterError("P3 requires a p3 mapping")
        treatment = p3.get("treatment")
        timeout = p3.get("object_delivery_timeout_ms")
        if treatment not in {"control_no_timeout", "object_delivery_timeout"}:
            raise P1AdapterError("p3.treatment must be control_no_timeout or object_delivery_timeout")
        if treatment == "control_no_timeout":
            if timeout is not None:
                raise P1AdapterError("control_no_timeout must omit p3.object_delivery_timeout_ms")
        elif not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
            raise P1AdapterError("object_delivery_timeout requires a positive integer p3.object_delivery_timeout_ms")
        relay, workload, runtime = (config.get(name) for name in ("relay", "workload", "runtime"))
        if not all(isinstance(value, Mapping) for value in (relay, workload, runtime)):
            raise P1AdapterError("P3 requires relay, workload, and runtime mappings")
        tracks = workload.get("track_ids")
        if not isinstance(tracks, list) or not tracks or not all(isinstance(item, str) for item in tracks):
            raise P1AdapterError("P3 workload.track_ids must be a non-empty list")
        priority = p3.get("subscriber_priority", 128)
        if not isinstance(priority, int) or not 0 <= priority <= 255:
            raise P1AdapterError("p3.subscriber_priority must be uint8")
        return cls(
            relay=dict(relay), namespace=namespace, seed=str(workload.get("seed", "")),
            user_id=str(runtime.get("subscriber_id", "subscriber_0")),
            connect_timeout_s=float(runtime.get("connect_timeout_s", 15)),
            receive_timeout_s=float(runtime.get("receive_timeout_s", 45)),
            initial_priorities={track: priority for track in tracks}, updated_priorities={},
            switch_at_ms=None, update_timeout_s=0.0,
            object_delivery_timeout_ms=timeout, treatment=treatment,
        )


class Moqt18P3Adapter(Moqt18P2Adapter):
    settings: P3AdapterSettings

    async def run_subscriber(self, planned: Iterable[PlannedObject], _switch_anchor_ns: int = 0) -> dict[str, Any]:
        from aiomoqt.client import MOQTClient
        from aiomoqt.types import FilterType

        objects, grouped, expected = self._objects(planned, self.settings.seed)
        relay = self.settings.relay_evidence("subscriber")
        validation = P1ReceiveValidation(expected, self.settings.seed, self.subscriber)
        resets: list[tuple[int, int, int]] = []
        started = time.monotonic_ns()
        async with MOQTClient(**self.settings.client_kwargs("subscriber")).connect() as sub_client:
            async with asyncio.timeout(self.settings.connect_timeout_s):
                await sub_client.client_session_init(timeout=max(1, int(self.settings.connect_timeout_s)))
            sub_probe = _probe_from_session(sub_client, relay)
            if not sub_probe.success:
                raise P1AdapterError(f"subscriber protocol negotiation failed: {sub_probe.reason}")

            def on_reset(stream_id: int, error_code: int) -> None:
                timestamp = time.monotonic_ns()
                resets.append((timestamp, stream_id, error_code))
                self.subscriber.recorder.append(Observation(
                    event_type="native_delivery_timeout_reset" if error_code == DELIVERY_TIMEOUT_ERROR else "native_stream_reset",
                    observed_ts_ns=timestamp, clock_domain_id="local-monotonic", run_id=objects[0].run_id,
                    user_id=self.settings.user_id, provenance="aiopquic_StreamReset_observer",
                    native_operation="QUIC RESET_STREAM", details={"stream_id": stream_id, "error_code": error_code},
                ))

            install_stream_reset_observer(sub_client, on_reset)
            sub_client.on_object_received = validation.on_object
            parameters = (object_delivery_timeout_parameters(self.settings.object_delivery_timeout_ms)
                          if self.settings.object_delivery_timeout_ms is not None else None)
            for track_id in sorted(grouped):
                response = await sub_client.subscribe(
                    namespace=self.settings.namespace, track_name=track_id,
                    priority=self.settings.initial_priorities[track_id], forward=1,
                    filter_type=FilterType.LATEST_OBJECT, parameters=parameters, wait_response=True,
                )
                if not isinstance(getattr(response, "request_id", None), int):
                    raise P1AdapterError(f"SUBSCRIBE response lacked request ID for {track_id}")
            try:
                async with asyncio.timeout(self.settings.receive_timeout_s):
                    while len(validation.seen) < len(expected):
                        await asyncio.sleep(0.01)
            except TimeoutError:
                pass
            sub_client.close()
        ended = time.monotonic_ns()
        timeout_resets = sum(code == DELIVERY_TIMEOUT_ERROR for _ts, _stream, code in resets)
        return {
            "protocol_negotiation_subscriber": sub_probe.as_dict(), "started_ts_ns": started, "ended_ts_ns": ended,
            "expected_objects": len(expected), "received_objects": len(validation.seen),
            "missing_objects": len(expected) - len(validation.seen), "duplicate_objects": validation.duplicate,
            "malformed_objects": validation.malformed, "unexpected_objects": validation.unexpected,
            "out_of_order_objects": validation.out_of_order, "native_stream_reset_count": len(resets),
            "native_delivery_timeout_reset_count": timeout_resets,
            "integrity_passed": not any((validation.duplicate, validation.malformed, validation.unexpected)),
        }
