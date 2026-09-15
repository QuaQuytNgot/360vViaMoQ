import unittest

from moq_360_protocol_lab.workload import SyntheticWorkload, deterministic_payload, plan_synthetic_objects


class WorkloadTests(unittest.TestCase):
    def test_deterministic_bytes_and_integer_budget(self):
        workload = SyntheticWorkload(
            track_ids=("track_a",), tile_ids=("tile_a",), per_track_bitrate_bps=(8_000_000_000,),
            group_duration_ns=1, group_count=3, objects_per_group=3, seed="seed",
        )
        objects = list(plan_synthetic_objects(workload, "run", 0))
        self.assertEqual(sum(item.payload_bytes for item in objects), 3)
        self.assertEqual(deterministic_payload("seed", "track_a", 0, 0, 8), deterministic_payload("seed", "track_a", 0, 0, 8))

    def test_mapping_length_is_checked(self):
        workload = SyntheticWorkload(
            track_ids=("track_a",), tile_ids=(), per_track_bitrate_bps=(1,),
            group_duration_ns=1, group_count=1, objects_per_group=1, seed="seed",
        )
        with self.assertRaises(ValueError):
            workload.validate()


if __name__ == "__main__":
    unittest.main()
