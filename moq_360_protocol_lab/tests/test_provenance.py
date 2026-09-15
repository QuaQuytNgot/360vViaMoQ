import json
import tempfile
import unittest
from pathlib import Path

from moq_360_protocol_lab.provenance import capture_provenance


class ProvenanceTests(unittest.TestCase):
    def test_draft18_provenance_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.yaml"
            capabilities = root / "capabilities.json"
            config.write_text("protocol: moqt18\n", encoding="utf-8")
            capabilities.write_text(json.dumps({}), encoding="utf-8")
            provenance = capture_provenance(
                Path.cwd(), config, capabilities,
                {"backend": "moqt18", "draft": 18, "transport": "raw_quic"},
                {"implementation": "candidate", "version": "v", "commit": "c"},
                "draft18_native",
            )
        protocol = provenance["protocol"]
        self.assertEqual(protocol["evaluated_protocol"], "draft-ietf-moq-transport-18")
        self.assertEqual(protocol["wire_protocol"], "moqt-18")
        self.assertEqual(protocol["transport_mode"], "raw_quic")
        self.assertEqual(provenance["relay"]["relay_commit"], "c")


if __name__ == "__main__":
    unittest.main()
