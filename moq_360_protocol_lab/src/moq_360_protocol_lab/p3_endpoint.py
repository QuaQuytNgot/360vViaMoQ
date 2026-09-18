"""One isolated endpoint for a native P3A Object Delivery Timeout run."""

from __future__ import annotations

import argparse
import asyncio
import json
import traceback
from pathlib import Path

from .config import load_yaml
from .manifest import read_manifest
from .p3_adapter import Moqt18P3Adapter, P3AdapterSettings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one strict draft-18 P3 endpoint in its assigned namespace.")
    parser.add_argument("--role", choices=("publisher", "subscriber"), required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    run_dir = args.run_dir.resolve()
    try:
        config = load_yaml(run_dir / "config.yaml")
        plan = json.loads((run_dir / "endpoint_plan.json").read_text(encoding="utf-8"))
        objects = list(read_manifest(run_dir / "manifest.jsonl"))
        run_id = plan["run_id"]
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("endpoint_plan run_id must be a non-empty string")
        settings = P3AdapterSettings.from_config(config, namespace=f"moq-360-p3/{run_id}")
        adapter = Moqt18P3Adapter(settings, run_dir / "publisher.events.jsonl", run_dir / "subscriber.events.jsonl", run_dir / "unused.events.jsonl")
        marker = run_dir / "subscriber_complete.marker"
        result = asyncio.run(adapter.run_publisher(objects, completion_marker=marker) if args.role == "publisher" else adapter.run_subscriber(objects))
        if args.role == "subscriber":
            marker.touch()
        (run_dir / f"{args.role}_result.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return 0
    except BaseException as exc:
        (run_dir / f"{args.role}_failure.json").write_text(json.dumps({"error_type": type(exc).__name__, "error": str(exc), "traceback": traceback.format_exc()}, indent=2) + "\n", encoding="utf-8")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
