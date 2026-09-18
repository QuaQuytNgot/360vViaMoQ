"""Small dependency-free P4 engineering summaries (no cache inference)."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


FIELDS = ["mode", "users", "overlap", "user_id", "t_first_object_ns", "t_first_group_ns", "viewport_activation_latency_ns", "pre_demand_upstream_payload_bytes", "publisher_to_relay_payload_bytes", "publisher_to_relay_wire_bytes", "relay_to_users_payload_bytes", "relay_to_users_wire_bytes", "upstream_amplification"]


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _bar_svg(path: Path, title: str, rows: list[dict[str, Any]], field: str) -> None:
    """Write a deliberately plain SVG; missing observations stay labelled NA."""
    labels = [f"{row.get('mode', '?')}/{row.get('user_id', '?')}" for row in rows]
    values = [_number(row.get(field)) for row in rows]
    maximum = max((value for value in values if value is not None), default=1.0)
    height = max(180, 80 + 32 * max(1, len(rows)))
    lines = [f'<svg xmlns="http://www.w3.org/2000/svg" width="900" height="{height}">',
             '<style>text{font:14px sans-serif}.bar{fill:#3973ac}.na{fill:#777}</style>',
             f'<text x="20" y="28">{title}</text>']
    for index, (label, value) in enumerate(zip(labels, values)):
        y = 55 + index * 30
        width = 0 if value is None else max(1, int(620 * value / maximum))
        lines.append(f'<text x="20" y="{y + 15}">{label}</text>')
        lines.append(f'<rect class="{"na" if value is None else "bar"}" x="220" y="{y}" width="{width}" height="18"/>')
        rendered = "NA" if value is None else f"{value:.3f}"
        lines.append(f'<text x="850" y="{y + 15}" text-anchor="end">{rendered}</text>')
    lines.append('</svg>')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aggregate completed P4 summary rows without changing raw runs.")
    parser.add_argument("--results", type=Path, default=Path("results/P4"))
    args = parser.parse_args(argv)
    rows: list[dict[str, Any]] = []
    for summary in sorted(args.results.glob("run_*/summary.json")):
        data = json.loads(summary.read_text(encoding="utf-8"))
        # Exploratory pre-instrumentation runs have no validity ledger and
        # must never be folded into the clean P4A campaign aggregate.
        if data.get("valid_for_protocol_claim") and isinstance(data.get("validity"), dict):
            rows.extend(data.get("rows", []))
    out = args.results / "p4_summary.csv"; out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS); writer.writeheader()
        for row in rows: writer.writerow({field: row.get(field) for field in FIELDS})
    analysis = args.results / "analysis"; analysis.mkdir(exist_ok=True)
    _bar_svg(analysis / "mode_vs_first_object.svg", "Mode → first useful Object latency (ns)", rows, "t_first_object_ns")
    _bar_svg(analysis / "mode_vs_first_group.svg", "Mode → first complete Group latency (ns)", rows, "t_first_group_ns")
    _bar_svg(analysis / "overlap_vs_publisher_relay_bytes.svg", "User overlap/config → publisher-to-relay wire bytes", rows, "publisher_to_relay_wire_bytes")
    _bar_svg(analysis / "overlap_vs_upstream_amplification.svg", "User overlap/config → upstream amplification", rows, "upstream_amplification")
    _bar_svg(analysis / "forward_activation_timeline.svg", "Forward activation: receiver endpoint latency from demand (ns)", rows, "t_first_object_ns")
    print(json.dumps({"summary": str(out), "analysis": str(analysis), "valid_rows": len(rows)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
