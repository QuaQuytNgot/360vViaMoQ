#!/usr/bin/env bash
# Delete only the three explicitly named P2 test namespaces and their veths.
set -euo pipefail
[[ ${EUID} -eq 0 ]] || { printf 'Run manually as root: sudo %s\n' "$0" >&2; exit 1; }
for ns in moq-p2-pub moq-p2-relay moq-p2-sub; do
  ip netns list | awk '{print $1}' | grep -Fxq "$ns" && ip netns del "$ns" || true
done
