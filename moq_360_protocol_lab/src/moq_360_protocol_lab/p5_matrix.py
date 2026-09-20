"""Run the intentionally small P5A/P5B native matrices."""

from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
from pathlib import Path

from .config import dump_yaml, load_yaml


MODES = ("live_only", "standalone_fetch_live", "joining_fetch")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a native P5 matrix serially and stop on the first invalid run")
    parser.add_argument("--series", choices=("P5A", "P5B"), required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        parser.error("run as root so the existing netns harness remains isolated")
    if args.repetitions < 1:
        parser.error("--repetitions must be positive")
    root = Path(__file__).resolve().parents[2]
    base = load_yaml(root / "configs" / ("p5a.base.draft18.yaml" if args.series == "P5A" else "p5b.base.draft18.yaml"))
    offsets = (100, 300, 500, 700, 900) if args.series == "P5A" else (100, 500, 900)
    with tempfile.TemporaryDirectory(prefix="moq-p5-matrix-") as temp:
        temp_path = Path(temp)
        for mode in args.modes:
            for offset in offsets:
                for repetition in range(1, args.repetitions + 1):
                    config = load_yaml(root / "configs" / ("p5a.base.draft18.yaml" if args.series == "P5A" else "p5b.base.draft18.yaml"))
                    config["p5"].update({"mode": mode, "demand_at_ms": 2000 + offset,
                                         "join_offset_ms": offset, "rep": repetition})
                    config["experiment"]["name"] = f"{args.series.lower()}-{mode}-{offset}ms-r{repetition}"
                    path = temp_path / f"{mode}-{offset}-r{repetition}.yaml"
                    dump_yaml(path, config)
                    print(f"P5 matrix: {args.series} {mode} offset={offset}ms rep={repetition}", flush=True)
                    completed = subprocess.run([str(root / "scripts" / "run_p5_experiment.sh"),
                                                "--config", str(path)], cwd=root)
                    if completed.returncode != 0:
                        print("P5 matrix stopped after invalid/failed run; evidence was preserved.", flush=True)
                        return completed.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

