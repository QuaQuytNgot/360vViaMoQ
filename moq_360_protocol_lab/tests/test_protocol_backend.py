import asyncio
import sys
import types
import unittest
from unittest.mock import patch

from moq_360_protocol_lab.backends import Moqt18Backend
from moq_360_protocol_lab.config import validate_for_run
from moq_360_protocol_lab.config import ConfigurationError
from moq_360_protocol_lab.protocol_probe import probe_config


def synthetic_config() -> dict[str, object]:
    return {
        "schema_version": 1,
        "protocol": {"backend": "moqt18", "draft": 18, "transport": "raw_quic"},
        "experiment": {"test_id": "P1", "name": "unit", "duration_s": 1},
        "runtime": {"mode": "synthetic_plan"},
        "workload": {
            "media_mode": "synthetic", "track_count": 1, "track_ids": ["track"],
            "tile_ids": ["tile"], "per_track_bitrate_bps": [1],
            "group_duration_ms": 1, "group_count": 1, "objects_per_group": 1, "seed": "unit",
        },
    }


class _FakeSession:
    def __init__(self, draft: int, alpn: str):
        self.negotiated_draft = draft
        self._quic = types.SimpleNamespace(_negotiated_alpn=lambda _unused: alpn)

    async def client_session_init(self, *, timeout: int) -> None:
        del timeout


class _FakeConnection:
    def __init__(self, session: _FakeSession):
        self.session = session

    async def __aenter__(self) -> _FakeSession:
        return self.session

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback


def fake_client_module(draft: int, alpn: str, calls: list[dict[str, object]]) -> types.ModuleType:
    module = types.ModuleType("aiomoqt.client")

    class FakeClient:
        def __init__(self, host: str, port: int, **kwargs: object):
            calls.append({"host": host, "port": port, **kwargs})

        def connect(self) -> _FakeConnection:
            return _FakeConnection(_FakeSession(draft, alpn))

    module.MOQTClient = FakeClient
    return module


class ProtocolSelectionTests(unittest.TestCase):
    def test_exact_draft_is_required_by_config(self):
        config = synthetic_config()
        self.assertEqual(validate_for_run(config), [])
        protocol = config["protocol"]
        assert isinstance(protocol, dict)
        protocol["draft"] = 16
        self.assertIn("protocol.draft must be exactly 18 for the moqt18 backend", validate_for_run(config))

    def test_moq_lite_is_not_a_draft18_alias(self):
        config = synthetic_config()
        protocol = config["protocol"]
        assert isinstance(protocol, dict)
        protocol.update({"backend": "moq_lite", "draft": 18})
        self.assertIn("protocol.draft must be exactly 5 for the moq_lite backend", validate_for_run(config))

    def test_probe_rejects_fallback_even_when_connection_succeeds(self):
        calls: list[dict[str, object]] = []
        client_module = fake_client_module(16, "moqt-16", calls)
        parent_module = types.ModuleType("aiomoqt")
        with patch.dict(sys.modules, {"aiomoqt": parent_module, "aiomoqt.client": client_module}):
            result = asyncio.run(Moqt18Backend().probe({"address": "relay", "port": 4433}))
        self.assertFalse(result.success)
        self.assertEqual(result.negotiated_draft, 16)
        self.assertEqual(result.alpn, "moqt-16")
        self.assertEqual(calls[0]["supported_drafts"], 18)
        self.assertTrue(calls[0]["use_quic"])

    def test_probe_accepts_exact_raw_quic_draft18_only(self):
        calls: list[dict[str, object]] = []
        client_module = fake_client_module(18, "moqt-18", calls)
        parent_module = types.ModuleType("aiomoqt")
        with patch.dict(sys.modules, {"aiomoqt": parent_module, "aiomoqt.client": client_module}):
            result = asyncio.run(Moqt18Backend().probe({"address": "relay", "port": 4433}))
        self.assertTrue(result.success)
        self.assertEqual(result.transport, "raw_quic")

    def test_standalone_probe_rejects_a_different_configured_draft_before_connecting(self):
        with self.assertRaises(ConfigurationError):
            asyncio.run(probe_config({
                "protocol": {"backend": "moqt18", "draft": 16, "transport": "raw_quic"},
                "relay": {"address": "relay", "port": 4433},
            }))


if __name__ == "__main__":
    unittest.main()
