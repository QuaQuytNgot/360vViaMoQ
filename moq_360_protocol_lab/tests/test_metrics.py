import unittest

from moq_360_protocol_lab.metrics import cross_tile_skew_ns, deadline_miss_ratio, missing_tiles_per_group, normalize_deliveries, p1_summary
from moq_360_protocol_lab.models import PlannedObject
from moq_360_protocol_lab.models import ObjectDelivery


def row(tile, completed_at):
    return ObjectDelivery(
        run_id="run", user_id="user", track_id=f"track_{tile}", tile_id=tile,
        group_id=0, object_id=0, scheduled_publish_ts_ns=100,
        complete_receive_ts_ns=completed_at, payload_bytes=10, completed=completed_at is not None,
    )


class MetricsTests(unittest.TestCase):
    def test_cross_tile_skew_requires_all_tiles(self):
        rows = [row("a", 120), row("b", 150)]
        self.assertEqual(cross_tile_skew_ns(rows, ("a", "b")), {("user", 0): 30})
        self.assertEqual(missing_tiles_per_group([row("a", 120)], ("a", "b")), {("user", 0): 1})

    def test_deadline_miss_ratio(self):
        self.assertEqual(deadline_miss_ratio([row("a", 120), row("b", 150)], 30), 0.5)

    def test_normalization_filters_users_and_marks_arrival_reordering(self):
        planned = [
            PlannedObject("run", "track", "tile", 0, 0, 0, 0, 1),
            PlannedObject("run", "track", "tile", 1, 0, 1, 1, 1),
        ]
        events = [
            {"event_type": "first_received", "observed_ts_ns": 10, "user_id": "wanted", "track_id": "track", "group_id": 1, "object_id": 0},
            {"event_type": "first_received", "observed_ts_ns": 11, "user_id": "other", "track_id": "track", "group_id": 0, "object_id": 0},
            {"event_type": "first_received", "observed_ts_ns": 12, "user_id": "wanted", "track_id": "track", "group_id": 0, "object_id": 0},
        ]
        rows = normalize_deliveries(planned, events, "wanted")
        self.assertTrue(rows[0].out_of_order)
        self.assertEqual(rows[0].first_receive_ts_ns, 12)

    def test_p1_summary_reports_goodput_completion_skew_and_percentiles(self):
        rows = [row("a", 120), row("b", 150)]
        summary = p1_summary(rows, ("a", "b"), 100, 200, deadline_ns=30)
        self.assertEqual(summary["aggregate_goodput_bps"], 1600000000.0)
        self.assertEqual(summary["weakest_track_goodput_bps"], 800000000.0)
        self.assertEqual(summary["group_completion_ratio"], 1.0)
        self.assertEqual(summary["cross_tile_completion_skew_ns"], {("user", 0): 30})
        latency = summary["completion_latency_ns"]
        assert isinstance(latency, dict)
        self.assertEqual(latency["p95"], 48.5)


if __name__ == "__main__":
    unittest.main()
