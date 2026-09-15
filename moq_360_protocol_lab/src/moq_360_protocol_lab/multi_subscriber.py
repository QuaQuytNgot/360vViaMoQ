"""Explicit multi-user demand definitions; no viewport predictor is included."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class ViewportDemand:
    user_id: str
    media_group_id: int
    required_tile_ids: tuple[str, ...]

    def validate(self) -> None:
        if not self.user_id or not self.required_tile_ids:
            raise ValueError("user_id and required_tile_ids are required")
        if self.media_group_id < 0:
            raise ValueError("media_group_id cannot be negative")


def validate_demands(demands: Mapping[str, list[ViewportDemand]], known_tiles: set[str]) -> None:
    for user_id, user_demands in demands.items():
        if not user_id:
            raise ValueError("user IDs cannot be empty")
        for demand in user_demands:
            demand.validate()
            unknown = set(demand.required_tile_ids) - known_tiles
            if unknown:
                raise ValueError(f"viewport demand refers to unknown tiles: {sorted(unknown)}")
