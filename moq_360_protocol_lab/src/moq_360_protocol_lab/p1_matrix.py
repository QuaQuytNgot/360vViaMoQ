"""Generate explicit P1 case configurations from one reviewed baseline."""

from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path

from .config import ConfigurationError, dump_yaml, load_yaml


def generate(baseline: Path, destination: Path, track_counts: list[int]) -> list[Path]:
    config = load_yaml(baseline)
    workload = config.get("workload")
    if not isinstance(workload, dict):
        raise ConfigurationError("baseline workload must be a mapping")
    offered = workload.get("total_offered_bitrate_mbps")
    if not isinstance(offered, (int, float)) or isinstance(offered, bool) or offered <= 0:
        raise ConfigurationError("baseline workload.total_offered_bitrate_mbps must be a positive number")
    total_bps = round(offered * 1_000_000)
    destination.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    for count in track_counts:
        if count <= 0:
            raise ConfigurationError("track counts must be positive")
        case = deepcopy(config)
        data = case["workload"]
        assert isinstance(data, dict)
        quotient, remainder = divmod(total_bps, count)
        data.update({
            "track_count": count,
            "track_ids": [f"track_{index}" for index in range(count)],
            "tile_ids": [f"tile_{index}" for index in range(count)],
            # Integer remainder distribution keeps the configured aggregate
            # rate exact without encoding any experiment rate in Python.
            "per_track_bitrate_bps": [quotient + (1 if index < remainder else 0) for index in range(count)],
            "aggregate_bitrate_bps": total_bps,
        })
        experiment = case.get("experiment")
        if isinstance(experiment, dict):
            experiment["name"] = f"P1-{count}-tracks"
        path = destination / f"p1_{count:02d}_tracks.yaml"
        dump_yaml(path, case)
        outputs.append(path)
    return outputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate reviewed P1 track-count case files.")
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--track-counts", nargs="+", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        for path in generate(args.baseline, args.output_dir, args.track_counts):
            print(path)
    except ConfigurationError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
