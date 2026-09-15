"""Append-only normalized observation storage."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Iterator

from .models import Observation


class EventRecorder:
    def __init__(self, destination: Path) -> None:
        self.destination = destination
        destination.parent.mkdir(parents=True, exist_ok=True)

    def append(self, observation: Observation) -> None:
        with self.destination.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(observation.as_dict(), sort_keys=True, separators=(",", ":")))
            handle.write("\n")


def read_events(path: Path) -> Iterator[dict]:
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if line.strip():
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid JSONL at {path}:{line_number}") from exc


def write_events(path: Path, events: Iterable[Observation]) -> None:
    recorder = EventRecorder(path)
    for event in events:
        recorder.append(event)
