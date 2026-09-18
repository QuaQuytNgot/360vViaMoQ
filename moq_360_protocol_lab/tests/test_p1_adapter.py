import unittest

from moq_360_protocol_lab.models import PlannedObject
from moq_360_protocol_lab.p1_adapter import P1AdapterError, decode_p1_identity, p1_payload


class P1PayloadTests(unittest.TestCase):
    def test_payload_carries_and_deterministically_verifies_identity(self):
        item = PlannedObject("run-1", "track-2", "tile-2", 3, 4, 0, 123, 512)
        payload = p1_payload(item, "seed")
        self.assertEqual(payload, p1_payload(item, "seed"))
        identity = decode_p1_identity(payload)
        self.assertEqual(identity["run_id"], "run-1")
        self.assertEqual(identity["track_id"], "track-2")
        self.assertEqual(identity["group_id"], 3)
        self.assertEqual(identity["object_id"], 4)
        self.assertEqual(identity["scheduled_publish_ts_ns"], 123)

    def test_payload_refuses_to_silently_drop_identity_metadata(self):
        item = PlannedObject("run", "track", "tile", 0, 0, 0, 0, 1)
        with self.assertRaises(P1AdapterError):
            p1_payload(item, "seed")


if __name__ == "__main__":
    unittest.main()
