import tempfile
import unittest
from pathlib import Path

from moq_360_protocol_lab.manifest import read_manifest
from moq_360_protocol_lab.media_manifest import build_fragment_manifest


class MediaManifestTests(unittest.TestCase):
    def test_indexes_dynamic_tile_quality_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "h264"
            fragment = root / "tile_r3_c4" / "q7" / "group_000002.m4s"
            fragment.parent.mkdir(parents=True)
            fragment.write_bytes(b"abc")
            output = Path(directory) / "manifest.jsonl"
            count = build_fragment_manifest(root, output, group_duration_ms=250)
            item = next(read_manifest(output))
            self.assertEqual(count, 1)
            self.assertEqual(item.track_id, "h264/tile_r3_c4/q7")
            self.assertEqual(item.group_id, 2)
            self.assertEqual(item.logical_media_time_ns, 500_000_000)
            self.assertEqual(item.payload_bytes, 3)


if __name__ == "__main__":
    unittest.main()
