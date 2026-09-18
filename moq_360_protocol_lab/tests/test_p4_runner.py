import csv
import tempfile
import unittest
from pathlib import Path

from moq_360_protocol_lab.models import PlannedObject
from moq_360_protocol_lab.p4_runner import _monitor_pass, classify_relay_errors, p4_group_rows


def _planned() -> list[PlannedObject]:
    return [PlannedObject("run", "track_0", "tile_0", 1, obj, 100 + obj, 1000, 10) for obj in range(3)]


class P4ValidityTests(unittest.TestCase):
    def test_teardown_session_closed_is_warning_only_after_teardown(self):
        result = classify_relay_errors("E requestUpdate failed: Session closed", teardown_started=True)
        self.assertEqual(result["teardown_warnings"], ["E requestUpdate failed: Session closed"])
        self.assertFalse(result["active_window_errors"])

    def test_active_window_error_invalidates(self):
        result = classify_relay_errors("E requestUpdate failed: rejected", teardown_started=True)
        self.assertTrue(result["active_window_errors"])

    def test_resource_error_is_fatal_even_during_teardown(self):
        result = classify_relay_errors("E Failed to create uni stream", teardown_started=True)
        self.assertTrue(result["resource_errors"])

    def test_monitor_artifact_requires_alive_relay_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.csv"
            path.write_text("relay_alive,publisher_alive,subscriber_alive\n0,0,0\n", encoding="utf-8")
            self.assertFalse(_monitor_pass(Path(directory)))
            path.write_text("relay_alive,publisher_alive,subscriber_alive\n1,1,1\n", encoding="utf-8")
            self.assertTrue(_monitor_pass(Path(directory)))

    def test_groups_require_every_expected_object_and_forward_zero_is_explicit(self):
        events = [{"event_type": "completed", "user_id": "u", "track_id": "track_0", "group_id": 1,
                   "object_id": obj, "observed_ts_ns": 200 + obj} for obj in range(2)]
        row = p4_group_rows(_planned(), events, "u", 0)[0]
        self.assertFalse(row["complete"])
        self.assertEqual(row["expected_forward"], 0)
        events.append({"event_type": "completed", "user_id": "u", "track_id": "track_0", "group_id": 1,
                       "object_id": 2, "observed_ts_ns": 202})
        row = p4_group_rows(_planned(), events, "u", 1)[0]
        self.assertTrue(row["complete"])
        self.assertEqual(row["completion_ts_ns"], 202)


if __name__ == "__main__":
    unittest.main()
