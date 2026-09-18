import asyncio
import unittest

from aiomoqt.context import profile_for
from aiomoqt.messages import RequestOk, RequestUpdate
from aiomoqt.types import ParamType
from aiomoqt.utils.buffer import Buffer

from moq_360_protocol_lab.aiomoqt_d18_update import (
    RequestUpdateUnavailable,
    send_forward_update,
    send_subscriber_priority_update,
    wait_for_update_response,
)


class _FakeSession:
    negotiated_draft = 18

    def __init__(self):
        self._bidi_streams = {2: 8}
        self._pending_requests = {}
        self._loop = asyncio.get_running_loop()
        self._next_request_id = 10
        self.sent = []

    def _allocate_request_id(self):
        request_id = self._next_request_id
        self._next_request_id += 2
        return request_id

    def send_stream_message(self, stream_id, message):
        self.sent.append((stream_id, message))


class Draft18RequestUpdateTests(unittest.IsolatedAsyncioTestCase):
    async def test_sender_uses_existing_subscription_stream_and_fresh_id(self):
        session = _FakeSession()
        sent = send_subscriber_priority_update(session, 2, 7)
        self.assertEqual(sent.request_id, 10)
        self.assertEqual(sent.subscription_request_id, 2)
        self.assertEqual(len(session.sent), 1)
        stream_id, message = session.sent[0]
        self.assertEqual(stream_id, 8)
        self.assertIsInstance(message, RequestUpdate)
        self.assertEqual(message.request_id, 10)
        self.assertEqual(message.existing_request_id, 2)
        self.assertEqual(message.parameters[ParamType.SUBSCRIBER_PRIORITY], 7)

    async def test_same_subscription_rejects_overlapping_update(self):
        session = _FakeSession()
        send_subscriber_priority_update(session, 2, 7)
        with self.assertRaises(RequestUpdateUnavailable):
            send_subscriber_priority_update(session, 2, 128)

    async def test_forward_uses_the_same_established_subscription_stream(self):
        session = _FakeSession()
        sent = send_forward_update(session, 2, 1)
        self.assertEqual(sent.subscription_request_id, 2)
        stream_id, message = session.sent[0]
        self.assertEqual(stream_id, 8)
        self.assertEqual(message.parameters[ParamType.FORWARD], 1)
        with self.assertRaises(ValueError):
            send_forward_update(session, 2, 2)

    async def test_multiple_subscription_updates_register_before_fast_responses(self):
        session = _FakeSession()
        session._bidi_streams[4] = 12
        first = send_subscriber_priority_update(session, 2, 7)
        second = send_subscriber_priority_update(session, 4, 9)
        # Both futures exist before the event loop can dispatch either reply.
        session._pending_requests[2].set_result(RequestOk(request_id=2, parameters={}))
        session._pending_requests[4].set_result(RequestOk(request_id=4, parameters={}))
        replies = await asyncio.gather(
            wait_for_update_response(session, first, timeout_s=1),
            wait_for_update_response(session, second, timeout_s=1),
        )
        self.assertEqual(len(replies), 2)
        self.assertFalse(session._pending_requests)

    async def test_response_is_correlated_to_original_stream_binding(self):
        session = _FakeSession()
        sent = send_subscriber_priority_update(session, 2, 7)
        task = asyncio.create_task(wait_for_update_response(session, sent, timeout_s=1))
        await asyncio.sleep(0)
        session._pending_requests[2].set_result(RequestOk(request_id=2, parameters={}))
        response, received_ts_ns = await task
        self.assertIsInstance(response, RequestOk)
        self.assertGreaterEqual(received_ts_ns, sent.sent_ts_ns)
        self.assertNotIn(2, session._pending_requests)
        self.assertFalse(session._moq360_priority_update_inflight)

    async def test_wire_format_omits_legacy_existing_request_id_for_draft18(self):
        profile = profile_for(18)
        message = RequestUpdate(
            request_id=42,
            existing_request_id=2,
            parameters={ParamType.SUBSCRIBER_PRIORITY: 7},
        )
        wire = message.serialize(prof=profile).data
        buffer = Buffer(data=wire)
        buffer.vi64 = True
        self.assertEqual(buffer.pull_vint(), 0x02)
        self.assertEqual(buffer.pull_uint16(), 4)
        decoded = RequestUpdate.deserialize(buffer, prof=profile)
        self.assertEqual(decoded.request_id, 42)
        self.assertIsNone(decoded.existing_request_id)
        self.assertEqual(decoded.parameters[ParamType.SUBSCRIBER_PRIORITY], 7)


if __name__ == "__main__":
    unittest.main()
