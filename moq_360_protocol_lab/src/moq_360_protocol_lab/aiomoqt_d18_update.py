"""Minimal, project-local draft-18 ``REQUEST_UPDATE`` sender.

aiomoqt 0.10.6 has the draft-18 message codec but no public sender for an
update on an established request bidi stream.  This module intentionally does
not alter aiomoqt's codec, transport, or scheduling.  It is a narrow adapter
over the installed release used by P2 and P4. P4 carries draft-18 ``FORWARD``
on the same established request stream; it does not emulate a transition by
re-subscribing or reconnecting.

The release associates all response frames on a subscription stream with the
original SUBSCRIBE request ID.  Draft-18 replies omit their Request ID, so a
single in-flight update per subscription is sufficient for correct local
response correlation.  Concurrent updates on the *same* subscription are
rejected rather than guessed or coalesced; updates on different subscriptions
remain independent.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any


class RequestUpdateUnavailable(RuntimeError):
    """The installed session cannot safely issue a conformant update."""


@dataclass(frozen=True)
class RequestUpdateSent:
    """Wire-send evidence; response and scheduling are intentionally separate."""

    request_id: int
    subscription_request_id: int
    encode_ts_ns: int
    sent_ts_ns: int


def _require_d18_subscription_stream(session: Any, subscription_request_id: int) -> int:
    if getattr(session, "negotiated_draft", None) != 18:
        raise RequestUpdateUnavailable("REQUEST_UPDATE extension requires a negotiated draft-18 session")
    streams = getattr(session, "_bidi_streams", None)
    if not isinstance(streams, dict):
        raise RequestUpdateUnavailable("aiomoqt session exposes no request-stream map")
    stream_id = streams.get(subscription_request_id)
    if not isinstance(stream_id, int):
        raise RequestUpdateUnavailable(
            f"no established request bidi stream for subscription {subscription_request_id}"
        )
    return stream_id


def _send_parameter_update(
    session: Any,
    subscription_request_id: int,
    parameters: dict[Any, int],
) -> RequestUpdateSent:
    """Serialize a draft-18 parameter update on its original bidi stream.

    This is deliberately send-only.  Call :func:`wait_for_update_response`
    before issuing another update for the same subscription.  The update
    consumes a fresh endpoint-owned Request ID, exactly as draft-18 §10.1
    requires; it is *not* sent as a control-stream message and does not open a
    replacement subscription stream.
    """
    if not isinstance(subscription_request_id, int) or subscription_request_id < 0:
        raise ValueError("subscription_request_id must be a non-negative integer")
    stream_id = _require_d18_subscription_stream(session, subscription_request_id)
    inflight = getattr(session, "_moq360_request_update_inflight", None)
    if inflight is None:
        inflight = set()
        setattr(session, "_moq360_request_update_inflight", inflight)
        # Retain P2's original private name as an alias. Both P2 priority and
        # P4 Forward updates share one per-subscription in-flight guard.
        setattr(session, "_moq360_priority_update_inflight", inflight)
    if subscription_request_id in inflight:
        raise RequestUpdateUnavailable(
            f"subscription {subscription_request_id} already has an in-flight REQUEST_UPDATE"
        )

    from aiomoqt.messages import RequestUpdate

    allocate = getattr(session, "_allocate_request_id", None)
    send = getattr(session, "send_stream_message", None)
    pending = getattr(session, "_pending_requests", None)
    loop = getattr(session, "_loop", None)
    if not callable(allocate) or not callable(send) or not isinstance(pending, dict) or loop is None:
        raise RequestUpdateUnavailable("installed aiomoqt session lacks required request-stream operations")
    # A d18 response carries the request stream binding (the original
    # SUBSCRIBE ID) rather than the fresh update ID.  Install the future *now*
    # so several updates on different subscription streams can be emitted
    # back-to-back without a fast response becoming an un-awaited event.
    if subscription_request_id in pending:
        raise RequestUpdateUnavailable(
            f"subscription {subscription_request_id} has an existing pending aiomoqt request response"
        )
    request_id = allocate()
    if not isinstance(request_id, int):
        raise RequestUpdateUnavailable("aiomoqt returned a non-integer Request ID")
    encode_ts_ns = time.monotonic_ns()
    message = RequestUpdate(
        request_id=request_id,
        # The d18 serializer correctly omits this legacy field.  Keeping the
        # relationship explicit here makes a wrong-profile regression visible.
        existing_request_id=subscription_request_id,
        parameters=parameters,
    )
    inflight.add(subscription_request_id)
    pending[subscription_request_id] = loop.create_future()
    try:
        send(stream_id, message)
    except BaseException:
        inflight.discard(subscription_request_id)
        pending.pop(subscription_request_id, None)
        raise
    return RequestUpdateSent(
        request_id=request_id,
        subscription_request_id=subscription_request_id,
        encode_ts_ns=encode_ts_ns,
        sent_ts_ns=time.monotonic_ns(),
    )


def send_subscriber_priority_update(
    session: Any, subscription_request_id: int, priority: int,
) -> RequestUpdateSent:
    """Send draft-18 ``SUBSCRIBER_PRIORITY`` using the shared P2/P4 path."""
    if not isinstance(priority, int) or not 0 <= priority <= 255:
        raise ValueError("subscriber priority must be an unsigned 8-bit integer")
    from aiomoqt.types import ParamType
    return _send_parameter_update(session, subscription_request_id, {ParamType.SUBSCRIBER_PRIORITY: priority})


def send_forward_update(
    session: Any, subscription_request_id: int, forward: int | bool,
) -> RequestUpdateSent:
    """Send one native draft-18 ``FORWARD`` update on the subscription stream."""
    if isinstance(forward, bool):
        forward = int(forward)
    if not isinstance(forward, int) or forward not in (0, 1):
        raise ValueError("Forward must be exactly 0 or 1")
    from aiomoqt.types import ParamType
    return _send_parameter_update(session, subscription_request_id, {ParamType.FORWARD: forward})


async def wait_for_update_response(
    session: Any,
    sent: RequestUpdateSent,
    *,
    timeout_s: float,
) -> tuple[Any, int]:
    """Await the one response on the established subscription stream.

    aiomoqt injects that stream's original SUBSCRIBE ID into draft-18 reply
    objects.  We therefore register the temporary future under the original
    ID.  This is safe only while exactly one update is outstanding on that
    stream, which the send helper enforces.
    """
    if not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
        raise ValueError("timeout_s must be positive")
    pending = getattr(session, "_pending_requests", None)
    loop = getattr(session, "_loop", None)
    if not isinstance(pending, dict) or loop is None:
        raise RequestUpdateUnavailable("installed aiomoqt session lacks response correlation state")
    key = sent.subscription_request_id
    future = pending.get(key)
    if future is None or not asyncio.isfuture(future):
        raise RequestUpdateUnavailable(
            f"subscription {key} has no pending future registered by REQUEST_UPDATE send"
        )
    try:
        async with asyncio.timeout(timeout_s):
            response = await future
        received_ts_ns = time.monotonic_ns()
        from aiomoqt.messages import RequestError, RequestOk

        if isinstance(response, RequestError):
            raise RequestUpdateUnavailable(
                f"REQUEST_ERROR code={getattr(response, 'error_code', None)} "
                f"reason={getattr(response, 'reason', '')}"
            )
        if not isinstance(response, RequestOk):
            raise RequestUpdateUnavailable(
                f"unexpected response to REQUEST_UPDATE: {type(response).__name__}"
            )
        return response, received_ts_ns
    except TimeoutError as exc:
        raise RequestUpdateUnavailable("REQUEST_UPDATE response timed out") from exc
    finally:
        pending.pop(key, None)
        inflight = getattr(session, "_moq360_request_update_inflight", set())
        inflight.discard(key)
