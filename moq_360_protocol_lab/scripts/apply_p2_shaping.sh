#!/usr/bin/env bash
# Apply a downstream-only P2 bottleneck inside moq-p2-relay, never on the host NIC.
set -euo pipefail

relay_ns=moq-p2-relay
device=p2rels0
capacity_mbps=
delay_ms=5
burst_kbit=64
latency_ms=1000
while [[ $# -gt 0 ]]; do
  case "$1" in
    --capacity-mbps) capacity_mbps=$2; shift 2 ;;
    --delay-ms) delay_ms=$2; shift 2 ;;
    --burst-kbit) burst_kbit=$2; shift 2 ;;
    --latency-ms) latency_ms=$2; shift 2 ;;
    -h|--help) printf 'Usage: %s --capacity-mbps N [--delay-ms N] [--burst-kbit N] [--latency-ms N]\n' "$0"; exit 0 ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done
[[ ${EUID} -eq 0 ]] || { printf 'Run manually as root: sudo %s ...\n' "$0" >&2; exit 1; }
[[ $capacity_mbps =~ ^[0-9]+([.][0-9]+)?$ ]] || { printf '--capacity-mbps must be positive\n' >&2; exit 2; }
[[ $delay_ms =~ ^[0-9]+$ && $burst_kbit =~ ^[1-9][0-9]*$ && $latency_ms =~ ^[1-9][0-9]*$ ]] || exit 2
ip netns list | awk '{print $1}' | grep -Fxq "$relay_ns" || { printf 'Namespace %s is absent\n' "$relay_ns" >&2; exit 1; }
ip -n "$relay_ns" link show "$device" >/dev/null

# TBF enforces rate. Its child netem adds a small fixed delay with zero loss.
tc -n "$relay_ns" qdisc replace dev "$device" root handle 1: tbf \
  rate "${capacity_mbps}mbit" burst "${burst_kbit}kb" latency "${latency_ms}ms"
tc -n "$relay_ns" qdisc replace dev "$device" parent 1:1 handle 10: netem delay "${delay_ms}ms" loss 0%
tc -n "$relay_ns" -s qdisc show dev "$device"
