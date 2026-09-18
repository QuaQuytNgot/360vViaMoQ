"""One endpoint process for a native P4 Forward-state run."""

from __future__ import annotations

import argparse
import asyncio
import json
import traceback
from pathlib import Path

from .config import load_yaml
from .manifest import read_manifest
from .p4_adapter import Moqt18P4Adapter, P4AdapterSettings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one strict draft-18 P4 endpoint.")
    parser.add_argument("--role", choices=("publisher", "subscriber"), required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    run_dir = args.run_dir.resolve()
    try:
        config = load_yaml(run_dir / "config.yaml")
        plan = json.loads((run_dir / "endpoint_plan.json").read_text(encoding="utf-8"))
        adapter = Moqt18P4Adapter(P4AdapterSettings.from_config(config, namespace=f"moq-360-p4/{plan['run_id']}"),
                                  run_dir / "publisher.events.jsonl", run_dir, run_dir / "forward_updates.events.jsonl")
        objects = list(read_manifest(run_dir / "manifest.jsonl"))
        result = asyncio.run(adapter.run_publisher(objects, completion_marker=run_dir / "subscriber_complete.marker")
                             if args.role == "publisher" else adapter.run_subscriber(objects, int(plan["source_anchor_ts_ns"])))
        if args.role == "subscriber":
            (run_dir / "subscriber_complete.marker").touch()
        (run_dir / f"{args.role}_result.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return 0
    except BaseException as exc:
        (run_dir / f"{args.role}_failure.json").write_text(json.dumps({"error_type": type(exc).__name__, "error": str(exc), "traceback": traceback.format_exc()}, indent=2) + "\n", encoding="utf-8")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
