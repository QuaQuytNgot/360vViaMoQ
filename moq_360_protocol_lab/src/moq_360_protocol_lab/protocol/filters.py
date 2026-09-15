"""Draft-18 FETCH/Joining-Fetch intent, not wire encoding.

The former fill/new-group labels were removed because they are not silently
mapped into the selected draft-18 baseline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


JoinSelection = Literal["live", "historical_group"]


@dataclass(frozen=True)
class LocationSelection:
    mode: JoinSelection
    group_id: int | None = None

    def validate(self) -> None:
        if self.mode == "historical_group" and self.group_id is None:
            raise ValueError("historical_group needs an explicit group_id")
        if self.mode == "live" and self.group_id is not None:
            raise ValueError("live selection cannot include a historical group_id")
        if self.group_id is not None and self.group_id < 0:
            raise ValueError("group_id cannot be negative")
