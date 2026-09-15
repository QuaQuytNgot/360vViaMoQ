"""Manifest storage for media or synthetic object plans."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Iterable, Iterator

from .models import PlannedObject


def write_manifest(path: Path, objects: Iterable[PlannedObject]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    seen: set[tuple[str, int, int]] = set()
    with path.open("w", encoding="utf-8") as handle:
        for item in objects:
            key = (item.track_id, item.group_id, item.object_id)
            if key in seen:
                raise ValueError(f"duplicate object location in manifest: {key}")
            seen.add(key)
            handle.write(json.dumps(item.as_dict(), sort_keys=True, separators=(",", ":")))
            handle.write("\n")


def read_manifest(path: Path) -> Iterator[PlannedObject]:
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                item["required_viewport_ids"] = tuple(item.get("required_viewport_ids", ()))
                yield PlannedObject(**item)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(f"Invalid manifest entry at {path}:{line_number}") from exc


def reanchor_manifest(source: Iterable[PlannedObject], run_id: str, anchor_ts_ns: int) -> Iterator[PlannedObject]:
    """Use source media timestamps but assign a fresh local live-release anchor."""
    for item in source:
        if item.logical_media_time_ns < 0:
            raise ValueError("media manifest contains a negative logical timestamp")
        yield replace(
            item,
            run_id=run_id,
            scheduled_publish_ts_ns=anchor_ts_ns + item.logical_media_time_ns,
        )
