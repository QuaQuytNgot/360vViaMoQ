"""One endpoint of an isolated native P2 experiment.

The root-only orchestration script launches this module separately in the
publisher and subscriber namespaces.  Splitting it here is deliberate: a
single process cannot originate both endpoints from their respective network
namespaces, and would invalidate a relay-to-subscriber bottleneck result.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import traceback
from pathlib import Path

from .config import load_yaml
from .manifest import read_manifest
from .p2_adapter import Moqt18P2Adapter, P2AdapterSettings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one strict draft-18 P2 endpoint in its assigned namespace.")
    parser.add_argument("--role", choices=("publisher", "subscriber"), required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    run_dir = args.run_dir.resolve()
    try:
        config = load_yaml(run_dir / "config.yaml")
        plan = json.loads((run_dir / "endpoint_plan.json").read_text(encoding="utf-8"))
        anchor_ns = plan["source_anchor_ts_ns"]
        if not isinstance(anchor_ns, int):
            raise ValueError("endpoint_plan source_anchor_ts_ns must be an integer")
        objects = list(read_manifest(run_dir / "manifest.jsonl"))
        run_id = plan["run_id"]
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("endpoint_plan run_id must be a non-empty string")
        settings = P2AdapterSettings.from_config(config, namespace=f"moq-360-p2/{run_id}")
        adapter = Moqt18P2Adapter(settings, run_dir / "publisher.events.jsonl", run_dir / "subscriber.events.jsonl", run_dir / "request_updates.events.jsonl")
        marker = run_dir / "subscriber_complete.marker"
        result = asyncio.run(
            adapter.run_publisher(objects, completion_marker=marker)
            if args.role == "publisher" else adapter.run_subscriber(objects, anchor_ns)
        )
        if args.role == "subscriber":
            # Marker contains no traffic or measurement data; it prevents the
            # publisher from closing the relay-facing QUIC session before the
            # relay drains its downstream queue.
            marker.touch()
        (run_dir / f"{args.role}_result.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return 0
    except BaseException as exc:
        (run_dir / f"{args.role}_failure.json").write_text(json.dumps({"error_type": type(exc).__name__, "error": str(exc), "traceback": traceback.format_exc()}, indent=2) + "\n", encoding="utf-8")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
