"""Dependency-free P5 engineering plots and valid-run aggregation."""

from __future__ import annotations

import argparse
import csv
import html
import json
from pathlib import Path
from typing import Any

from .p5_runner import P5_SUMMARY_FIELDS


COLORS = {"live_only": "#3973ac", "standalone_fetch_live": "#d9822b", "joining_fetch": "#238636"}


def _markdown_table(path: Path, rows: list[dict[str, Any]], series: str) -> None:
    if series == "P5A":
        fields = [("Mode", "mode"), ("Join offset", "join_offset_ms"), ("Rep", "rep"),
                  ("First Object ms", "first_object_ms"),
                  ("First Complete Group ms", "first_complete_group_ms"),
                  ("Historical bytes", "historical_bytes"),
                  ("Redundant bytes", "redundant_bytes"),
                  ("Missing Objects", "missing_objects"),
                  ("Live-edge delay ms", "live_edge_delay_ms"), ("Valid", "valid")]
    else:
        fields = [("Mode", "mode"), ("Join offset", "join_offset_ms"),
                  ("Tracks", "tracks"), ("Rep", "rep"),
                  ("Set First Object ms", "set_first_object_ms"),
                  ("Set First Group ms", "set_first_group_ms"),
                  ("Group Skew ms", "group_skew_ms"),
                  ("Historical bytes", "historical_bytes"),
                  ("Redundant bytes", "redundant_bytes"), ("Valid", "valid")]
    selected = sorted((row for row in rows if row.get("series") == series),
                      key=lambda row: (str(row.get("mode")), int(row.get("join_offset_ms", 0)),
                                       int(row.get("rep", 0))))
    lines = [f"# {series} individual valid repetitions", "",
             "| " + " | ".join(label for label, _field in fields) + " |",
             "|" + "|".join("---" for _ in fields) + "|"]
    lines.extend("| " + " | ".join(str(row.get(field, "")) for _label, field in fields) + " |"
                 for row in selected)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _line_svg(path: Path, title: str, rows: list[dict[str, Any]], field: str,
              *, series: str, ylabel: str) -> None:
    selected = [row for row in rows if row.get("series") == series and isinstance(row.get(field), (int, float))]
    width, height = 900, 520
    left, right, top, bottom = 90, 30, 55, 70
    x_values = [float(row["join_offset_ms"]) for row in selected]
    y_values = [float(row[field]) for row in selected]
    xmax = max(x_values, default=1000.0); ymax = max(y_values, default=1.0) or 1.0
    lines = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">',
             '<style>text{font:13px sans-serif}.axis{stroke:#333}.grid{stroke:#ddd}.pt{stroke:#fff;stroke-width:1}</style>',
             f'<text x="{width/2}" y="28" text-anchor="middle">{html.escape(title)}</text>',
             f'<line class="axis" x1="{left}" y1="{height-bottom}" x2="{width-right}" y2="{height-bottom}"/>',
             f'<line class="axis" x1="{left}" y1="{top}" x2="{left}" y2="{height-bottom}"/>',
             f'<text x="{width/2}" y="{height-18}" text-anchor="middle">join offset (ms)</text>',
             f'<text x="18" y="{height/2}" transform="rotate(-90 18 {height/2})" text-anchor="middle">{html.escape(ylabel)}</text>']
    for mode in ("live_only", "standalone_fetch_live", "joining_fetch"):
        points = sorted((float(row["join_offset_ms"]), float(row[field])) for row in selected if row.get("mode") == mode)
        coords = []
        for x, y in points:
            px = left + (width-left-right) * x / xmax
            py = height-bottom - (height-top-bottom) * y / ymax
            coords.append((px, py))
        if coords:
            lines.append(f'<polyline fill="none" stroke="{COLORS[mode]}" opacity="0.55" points="' +
                         " ".join(f"{x:.1f},{y:.1f}" for x, y in coords) + '"/>')
            for x, y in coords:
                lines.append(f'<circle class="pt" cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{COLORS[mode]}"/>')
    for index, mode in enumerate(COLORS):
        lines.append(f'<rect x="{620 + index*90}" y="40" width="12" height="12" fill="{COLORS[mode]}"/>')
        lines.append(f'<text x="{636 + index*90}" y="51">{html.escape(mode)}</text>')
    lines.append('</svg>')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _timeline(path: Path, results: Path, rows: list[dict[str, Any]]) -> None:
    chosen = [row for row in rows if row.get("series") == "P5A" and row.get("join_offset_ms") == 500]
    width, height = 900, 130 + 70 * len(chosen)
    max_ms = max((float(row.get("first_complete_group_ms") or 0) for row in chosen), default=1.0) or 1.0
    lines = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">',
             '<style>text{font:13px sans-serif}.axis{stroke:#333}.tick{stroke:#bbb}</style>',
             '<text x="450" y="28" text-anchor="middle">Representative 500-ms join timeline (individual repetitions)</text>']
    for index, row in enumerate(chosen):
        y = 70 + index * 60
        mode = str(row["mode"])
        lines.append(f'<text x="15" y="{y+5}">{html.escape(mode)} r{row.get("rep")}</text>')
        lines.append(f'<line class="axis" x1="180" y1="{y}" x2="860" y2="{y}"/>')
        for label, field, color in (("first Object", "first_object_ms", "#d9822b"),
                                    ("complete Group", "first_complete_group_ms", "#238636")):
            value = row.get(field)
            if isinstance(value, (int, float)):
                x = 180 + 680 * float(value) / max_ms
                lines.append(f'<circle cx="{x:.1f}" cy="{y}" r="5" fill="{color}"/>')
                lines.append(f'<text x="{x:.1f}" y="{y-9}" text-anchor="middle">{label} {float(value):.1f} ms</text>')
    lines.append('</svg>')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aggregate valid P5 runs and create engineering SVGs")
    parser.add_argument("--results", type=Path, default=Path("results/P5"))
    args = parser.parse_args(argv)
    rows = []
    for path in sorted(args.results.glob("run_*/summary.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("valid_for_protocol_claim"):
            rows.append({field: data.get(field) for field in P5_SUMMARY_FIELDS})
    # p5_summary.csv is the append-only raw ledger written by p5_runner.
    # Analysis never rewrites it or removes invalid runs.
    analysis = args.results / "analysis"; analysis.mkdir(exist_ok=True)
    out = analysis / "p5_valid_summary.csv"
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=P5_SUMMARY_FIELDS); writer.writeheader(); writer.writerows(rows)
    _markdown_table(analysis / "p5a_summary.md", rows, "P5A")
    _markdown_table(analysis / "p5b_summary.md", rows, "P5B")
    _line_svg(analysis / "p5a_join_offset_vs_first_object.svg", "P5A join offset vs first Object latency", rows, "first_object_ms", series="P5A", ylabel="latency (ms)")
    _line_svg(analysis / "p5a_join_offset_vs_first_complete_group.svg", "P5A join offset vs first complete Group latency", rows, "first_complete_group_ms", series="P5A", ylabel="latency (ms)")
    _line_svg(analysis / "p5a_join_offset_vs_historical_bytes.svg", "P5A join offset vs historical bytes", rows, "historical_bytes", series="P5A", ylabel="bytes")
    _line_svg(analysis / "p5a_join_offset_vs_live_edge_delay.svg", "P5A join offset vs live-edge delay", rows, "live_edge_delay_ms", series="P5A", ylabel="delay (ms)")
    _timeline(analysis / "p5a_middle_join_timeline.svg", args.results, rows)
    _line_svg(analysis / "p5b_join_offset_vs_set_first_group.svg", "P5B join offset vs set first-complete-Group latency", rows, "set_first_group_ms", series="P5B", ylabel="latency (ms)")
    _line_svg(analysis / "p5b_join_offset_vs_group_skew.svg", "P5B join offset vs cross-track Group completion skew", rows, "group_skew_ms", series="P5B", ylabel="skew (ms)")
    print(json.dumps({"summary": str(out), "analysis": str(analysis), "valid_rows": len(rows)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
