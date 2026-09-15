"""Index pre-fragmented tiled media without selecting experiment parameters."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from .manifest import write_manifest
from .models import PlannedObject


GROUP_NAME = re.compile(r"^group_([0-9]+)\.m4s$")
TILE_NAME = re.compile(r"^tile_r[0-9]+_c[0-9]+$")


def build_fragment_manifest(fragment_root: Path, output: Path, group_duration_ms: int, *, run_id: str = "media_source") -> int:
    """Index `tile_rR_cC/qN/group_XXXXXX.m4s` under one codec fragment root.

    The duration is mandatory because a filename alone cannot prove timing.
    The object payload remains a fragment file; no media bytes are decoded.
    """
    if group_duration_ms <= 0:
        raise ValueError("group_duration_ms must be positive")
    if not fragment_root.is_dir():
        raise ValueError(f"fragment root is not a directory: {fragment_root}")
    objects: list[PlannedObject] = []
    for fragment in sorted(fragment_root.glob("tile_r*_c*/q*/group_*.m4s")):
        tile_id = fragment.parent.parent.name
        quality_id = fragment.parent.name
        match = GROUP_NAME.match(fragment.name)
        if not TILE_NAME.match(tile_id) or not match:
            continue
        group_id = int(match.group(1))
        codec = fragment_root.name
        logical_time_ns = group_id * group_duration_ms * 1_000_000
        objects.append(PlannedObject(
            run_id=run_id,
            track_id=f"{codec}/{tile_id}/{quality_id}",
            tile_id=tile_id,
            group_id=group_id,
            object_id=0,
            logical_media_time_ns=logical_time_ns,
            scheduled_publish_ts_ns=logical_time_ns,
            payload_bytes=fragment.stat().st_size,
            content_id=f"file:{fragment.resolve()}",
        ))
    if not objects:
        raise ValueError("no group_*.m4s files found below tile_rR_cC/qN")
    write_manifest(output, objects)
    return len(objects)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a raw MoQ workload manifest from tiled fMP4 fragments.")
    parser.add_argument("--fragment-root", type=Path, required=True, help="One codec root, e.g. media/fragmented/h264")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--group-duration-ms", type=int, required=True)
    parser.add_argument("--run-id", default="media_source")
    args = parser.parse_args(argv)
    try:
        count = build_fragment_manifest(args.fragment_root, args.output, args.group_duration_ms, run_id=args.run_id)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"indexed_objects={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
