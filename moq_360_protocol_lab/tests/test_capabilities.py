import json
import tempfile
import unittest
from pathlib import Path

from moq_360_protocol_lab.capabilities import gate_test


class CapabilityTests(unittest.TestCase):
    def test_synthetic_is_explicitly_non_protocol(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capabilities.json"
            path.write_text(json.dumps({"implementation": {}, "features": {}}), encoding="utf-8")
            gate = gate_test("P1", "synthetic_plan", path)
            self.assertTrue(gate.runnable)
            self.assertEqual(gate.status, "COMPLETED_SYNTHETIC")

    def test_live_p2_is_gated_when_request_update_is_not_smoke_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capabilities.json"
            path.write_text(json.dumps({
                "backends": {"moqt18": {}},
                "features": {
                    "relay_p2_smoke": {"status": "blocked"},
                },
            }), encoding="utf-8")
            gate = gate_test("P2", "live", path, probe_succeeded=True)
            self.assertFalse(gate.runnable)
            self.assertIn("relay_p2_smoke: blocked", gate.reasons)


if __name__ == "__main__":
    unittest.main()
