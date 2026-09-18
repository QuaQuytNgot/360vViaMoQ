import pytest

from moq_360_protocol_lab.aiomoqt_d18_timeout import (
    DELIVERY_TIMEOUT_ERROR,
    install_stream_reset_observer,
    object_delivery_timeout_parameters,
)


def test_object_timeout_wire_parameter_is_d18_key_two() -> None:
    assert object_delivery_timeout_parameters(75) == {0x02: 75}
    assert object_delivery_timeout_parameters(0) == {0x02: 0}
    with pytest.raises(ValueError):
        object_delivery_timeout_parameters(-1)


def test_stream_reset_observer_observes_without_replacing_dispatch() -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.dispatched = []

        def quic_event_received(self, event):
            self.dispatched.append(event)
            return "preserved"

    session, resets = FakeSession(), []
    install_stream_reset_observer(session, lambda stream_id, error_code: resets.append((stream_id, error_code)))
    # A normal concrete event has a class name; use a tiny type rather than
    # importing the transport implementation in this unit test.
    event = type("StreamReset", (), {"stream_id": 12, "error_code": DELIVERY_TIMEOUT_ERROR})()
    assert session.quic_event_received(event) == "preserved"
    assert resets == [(12, DELIVERY_TIMEOUT_ERROR)]
    assert session.dispatched == [event]
