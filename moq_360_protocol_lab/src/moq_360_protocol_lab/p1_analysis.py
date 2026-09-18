"""Dependency-free, intentionally simple P1 summary and SVG plots."""

from __future__ import annotations

import argparse
import csv
import html
import json
from pathlib import Path
from typing import Iterable


def _number(row: dict[str, str], key: str) -> float | None:
    try:
        return float(row[key]) if row.get(key) not in {None, ""} else None
    except ValueError:
        return None


def _plot(path: Path, rows: list[dict[str, str]], title: str, fields: Iterable[tuple[str, str]]) -> None:
    width, height, margin = 720, 430, 60
    series = [(label, field, [(float(row["track_count"]), _number(row, field)) for row in rows if _number(row, field) is not None]) for label, field in fields]
    points = [point for _label, _field, values in series for point in values]
    if not points:
        return
    xs, ys = [point[0] for point in points], [point[1] for point in points]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    if xmin == xmax: xmax += 1
    if ymin == ymax: ymax = ymin + 1
    def x(value: float) -> float: return margin + (value - xmin) / (xmax - xmin) * (width - 2 * margin)
    def y(value: float) -> float: return height - margin - (value - ymin) / (ymax - ymin) * (height - 2 * margin)
    colors = ["#1f77b4", "#d62728", "#2ca02c"]
    body = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
            '<rect width="100%" height="100%" fill="white"/>', f'<text x="{margin}" y="30" font-size="18">{html.escape(title)}</text>',
            f'<line x1="{margin}" y1="{height-margin}" x2="{width-margin}" y2="{height-margin}" stroke="black"/>',
            f'<line x1="{margin}" y1="{margin}" x2="{margin}" y2="{height-margin}" stroke="black"/>',
            f'<text x="{margin}" y="{height-18}" font-size="12">tracks {xmin:g}–{xmax:g}</text>',
            f'<text x="{width-190}" y="{height-18}" font-size="12">value {ymin:.3g}–{ymax:.3g}</text>']
    for index, (label, _field, values) in enumerate(series):
        if not values: continue
        color = colors[index % len(colors)]
        polyline = " ".join(f"{x(px):.1f},{y(py):.1f}" for px, py in values)
        body += [f'<polyline fill="none" stroke="{color}" stroke-width="2" points="{polyline}"/>',
                 f'<text x="{width-190}" y="{55 + index*18}" fill="{color}" font-size="12">{html.escape(label)}</text>']
    body.append("</svg>")
    path.write_text("\n".join(body), encoding="utf-8")


def analyze(summary_csv: Path, output_dir: Path) -> None:
    with summary_csv.open(newline="", encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle) if row.get("valid_for_protocol_claim") == "True"]
    # `p1_summary.csv` deliberately retains valid smoke rows as raw evidence.
    # The characterization table and plots must instead contain only runs
    # whose per-run evidence identifies them as measurements.
    rows = [
        row for row in rows
        if (summary_csv.parent / row["run_id"] / "summary.json").is_file()
        and json.loads(
            (summary_csv.parent / row["run_id"] / "summary.json").read_text(encoding="utf-8")
        ).get("operation") == "p1_measurement"
    ]
    rows.sort(key=lambda row: int(row["track_count"]))
    output_dir.mkdir(parents=True, exist_ok=True)
    columns = ["track_count", "network_capacity_mbps", "aggregate_goodput_bps", "weakest_track_goodput_bps", "median_completion_latency_ms", "p95_completion_latency_ms", "median_skew_ms", "p95_skew_ms", "group_completion_ratio", "deadline_miss_ratio"]
    table = ["# P1 summary", "", "| " + " | ".join(columns) + " |", "|" + "|".join("---" for _ in columns) + "|"]
    table += ["| " + " | ".join(row.get(column, "") for column in columns) + " |" for row in rows]
    (output_dir / "p1_summary.md").write_text("\n".join(table) + "\n", encoding="utf-8")
    _plot(output_dir / "track_count_vs_skew.svg", rows, "Track count vs completion skew (ms)", (("median skew", "median_skew_ms"), ("p95 skew", "p95_skew_ms")))
    _plot(output_dir / "track_count_vs_weakest_goodput.svg", rows, "Track count vs weakest-track goodput (bps)", (("weakest goodput", "weakest_track_goodput_bps"),))
    _plot(output_dir / "track_count_vs_group_completion.svg", rows, "Track count vs Group completion ratio", (("Group completion", "group_completion_ratio"),))
    _plot(output_dir / "track_count_vs_latency.svg", rows, "Track count vs completion latency (ms)", (("median latency", "median_completion_latency_ms"), ("p95 latency", "p95_completion_latency_ms")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a compact P1 table and simple SVG plots.")
    parser.add_argument("--summary", default=Path("results/P1/p1_summary.csv"), type=Path)
    parser.add_argument("--output-dir", default=Path("results/P1/analysis"), type=Path)
    args = parser.parse_args(argv)
    analyze(args.summary, args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
