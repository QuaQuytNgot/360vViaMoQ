import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from aiomoqt.types import ObjectStatus

from moq_360_protocol_lab.models import PlannedObject
from moq_360_protocol_lab.p5_adapter import _P5ReceiveValidation
from moq_360_protocol_lab.p5_runner import (
    _first_complete_set,
    _live_edge_delay_ms,
    _transition_rows,
)


def _object(track: str, group: int, object_id: int, media: int, scheduled: int) -> PlannedObject:
    return PlannedObject("run", track, track.replace("track", "tile"), group,
                         object_id, media, scheduled, 100)


class P5MetricTests(unittest.TestCase):
    def test_status_object_is_protocol_metadata_not_malformed_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            validator = _P5ReceiveValidation({}, "seed", "run",
                root / "subscriber.jsonl", root / "transition.jsonl")
            validator.on_fetch(SimpleNamespace(payload=b"", group_id=1, object_id=4,
                status=ObjectStatus.END_OF_GROUP), 0, 0, 2)
            self.assertEqual(validator.status_objects, 1)
            self.assertEqual(validator.malformed, 0)

    def test_set_completion_requires_same_group_on_every_track(self):
        rows = [
            {"track_id": "track_0", "group_id": 2, "complete": True,
             "completion_ts_ns": 200},
            {"track_id": "track_1", "group_id": 3, "complete": True,
             "completion_ts_ns": 210},
        ]
        self.assertEqual(_first_complete_set(rows, ["track_0", "track_1"], 100), {})
        rows.append({"track_id": "track_1", "group_id": 2, "complete": True,
                     "completion_ts_ns": 240})
        selected = _first_complete_set(rows, ["track_0", "track_1"], 100)
        self.assertEqual({row["group_id"] for row in selected.values()}, {2})

    def test_live_edge_delay_uses_slowest_required_track(self):
        planned = [_object("track_0", 0, 0, 0, 10),
                   _object("track_0", 0, 1, 25, 20),
                   _object("track_1", 0, 0, 0, 10),
                   _object("track_1", 0, 1, 25, 20)]
        received = [
            {"track_id": "track_0", "group_id": 0, "object_id": 1, "received_ts_ns": 30},
            {"track_id": "track_1", "group_id": 0, "object_id": 0, "received_ts_ns": 30},
        ]
        self.assertEqual(_live_edge_delay_ms(planned, received, 40), 25 / 1_000_000)

    def test_transition_table_retains_unexpected_identities(self):
        planned = [_object("track_0", 0, 0, 0, 10)]
        rows = _transition_rows(planned, [], 10,
            {"track_0": {"group_id": 0}},
            [{"track_id": "alien", "group_id": 9, "object_id": 7,
              "received_ts_ns": 20, "payload_bytes": 12}])
        self.assertEqual(rows[-1]["classification"], "unexpected")
        self.assertEqual(rows[-1]["track_id"], "alien")


if __name__ == "__main__":
    unittest.main()
