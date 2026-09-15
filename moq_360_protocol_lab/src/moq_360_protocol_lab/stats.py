"""Small dependency-free summary statistics with explicit unavailable values."""

from __future__ import annotations

import math
from statistics import mean, median
from typing import Iterable, Optional


def percentile(values: Iterable[float], fraction: float) -> Optional[float]:
    """Linear interpolation over sorted samples; returns None for no samples."""
    samples = sorted(float(value) for value in values)
    if not samples:
        return None
    if not 0 <= fraction <= 1:
        raise ValueError("percentile fraction must be in [0, 1]")
    position = (len(samples) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return samples[lower]
    return samples[lower] + (samples[upper] - samples[lower]) * (position - lower)


def summarize(values: Iterable[float]) -> dict[str, Optional[float] | int]:
    samples = [float(value) for value in values]
    if not samples:
        return {"count": 0, "mean": None, "median": None, "p95": None, "p99": None, "min": None, "max": None}
    return {
        "count": len(samples),
        "mean": mean(samples),
        "median": median(samples),
        "p95": percentile(samples, 0.95),
        "p99": percentile(samples, 0.99),
        "min": min(samples),
        "max": max(samples),
    }
