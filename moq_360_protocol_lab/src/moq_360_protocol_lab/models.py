"""Stable, protocol-neutral records used by the experiment harness.

These are laboratory records, not guessed MOQT wire structures.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Optional, Sequence


@dataclass(frozen=True)
class PlannedObject:
    run_id: str
    track_id: str
    tile_id: str
    group_id: int
    object_id: int
    logical_media_time_ns: int
    scheduled_publish_ts_ns: int
    payload_bytes: int
    decoder_safe: Optional[bool] = None
    required_viewport_ids: tuple[str, ...] = ()
    content_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["required_viewport_ids"] = list(self.required_viewport_ids)
        return result


@dataclass(frozen=True)
class Observation:
    event_type: str
    observed_ts_ns: int
    clock_domain_id: str
    run_id: str
    provenance: str
    user_id: Optional[str] = None
    track_id: Optional[str] = None
    tile_id: Optional[str] = None
    group_id: Optional[int] = None
    object_id: Optional[int] = None
    payload_bytes: Optional[int] = None
    correlation_id: Optional[str] = None
    native_operation: Optional[str] = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["details"] = dict(self.details)
        return result


@dataclass(frozen=True)
class ObjectDelivery:
    run_id: str
    user_id: str
    track_id: str
    tile_id: str
    group_id: int
    object_id: int
    scheduled_publish_ts_ns: int
    actual_publish_ts_ns: Optional[int] = None
    first_receive_ts_ns: Optional[int] = None
    complete_receive_ts_ns: Optional[int] = None
    payload_bytes: int = 0
    completed: bool = False
    missing: bool = False
    duplicate: bool = False
    dropped: bool = False
    expired: bool = False
    reset: bool = False
    out_of_order: bool = False
    subscriber_priority: Optional[int] = None
    publisher_priority: Optional[int] = None
    provenance: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CapabilityFinding:
    name: str
    status: str
    evidence: tuple[str, ...] = ()
    native_mapping: Optional[str] = None
    notes: Optional[str] = None


@dataclass(frozen=True)
class GateResult:
    runnable: bool
    status: str
    reasons: tuple[str, ...]


def require_keys(data: Mapping[str, Any], keys: Sequence[str]) -> list[str]:
    """Return missing / empty keys without assigning experimental defaults."""
    missing: list[str] = []
    for key in keys:
        value = data.get(key)
        if value is None or value == "" or value == []:
            missing.append(key)
    return missing
