"""Narrow draft-18 Object Delivery Timeout extension for the P3 adapter.

This module does not implement a timer.  It only sends the standard d18
subscriber parameter accepted by aiomoqt's generic public ``subscribe`` API
and exposes the native QUIC RESET_STREAM event that aiomoqt otherwise cleans
up internally.  The relay remains solely responsible for expiry/reset.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


DELIVERY_TIMEOUT_ERROR = 0x02


def object_delivery_timeout_parameters(timeout_ms: int) -> dict[int, int]:
    """Return the d18 OBJECT_DELIVERY_TIMEOUT (`0x02`) request parameter."""
    if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or timeout_ms < 0:
        raise ValueError("object delivery timeout must be a non-negative integer milliseconds value")
    # aiomoqt exposes this key as ParamType.DELIVERY_TIMEOUT.  Keep the wire
    # integer here so this module remains explicit about the d18 key.
    return {0x02: timeout_ms}


def install_stream_reset_observer(session: Any, callback: Callable[[int, int], None]) -> None:
    """Observe, but do not alter, native QUIC StreamReset processing.

    aiomoqt 0.10.6 logs and cleans StreamReset without an application callback.
    Wrapping the instance dispatcher preserves its original processing and
    merely makes stream ID/error code measurable by the P3 subscriber.
    """
    if getattr(session, "_moq360_stream_reset_observer_installed", False):
        raise RuntimeError("a stream-reset observer is already installed")
    original = session.quic_event_received

    def observed(event: Any) -> Any:
        # Avoid importing aiopquic at module import time; the production
        # protocol already supplies the concrete event object.
        if event.__class__.__name__ == "StreamReset":
            stream_id, error_code = getattr(event, "stream_id", None), getattr(event, "error_code", None)
            if isinstance(stream_id, int) and isinstance(error_code, int):
                callback(stream_id, error_code)
        return original(event)

    session.quic_event_received = observed
    session._moq360_stream_reset_observer_installed = True
