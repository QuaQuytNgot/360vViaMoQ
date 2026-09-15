import unittest

from moq_360_protocol_lab.pacing import LivePacer


class PacingTests(unittest.TestCase):
    def test_fixed_target_and_lateness(self):
        readings = iter((50, 100))
        sleeps = []
        pacer = LivePacer(anchor_ts_ns=100, clock=lambda: next(readings), sleeper=sleeps.append)
        result = pacer.wait_for(100)
        self.assertEqual(sleeps, [50 / 1_000_000_000])
        self.assertEqual(result.actual_ts_ns, 100)
        self.assertEqual(result.lateness_ns, 0)

    def test_negative_logical_time_is_rejected(self):
        pacer = LivePacer(anchor_ts_ns=1, clock=lambda: 1)
        with self.assertRaises(ValueError):
            pacer.scheduled_time(-1)


if __name__ == "__main__":
    unittest.main()
