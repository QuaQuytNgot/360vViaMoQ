#!/usr/bin/env bash
# Remove only the qdisc created on the P2 relay namespace downstream veth.
set -euo pipefail
relay_ns=moq-p2-relay
device=p2rels0
[[ ${EUID} -eq 0 ]] || { printf 'Run manually as root: sudo %s\n' "$0" >&2; exit 1; }
ip netns list | awk '{print $1}' | grep -Fxq "$relay_ns" || exit 0
ip -n "$relay_ns" qdisc del dev "$device" root 2>/dev/null || true
