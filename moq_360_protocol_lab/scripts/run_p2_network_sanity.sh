#!/usr/bin/env bash
# Measure the relay->subscriber veth capacity without using MOQT traffic.
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
python="$root/.venv/bin/python"
[[ -x $python ]] || { printf 'Missing project virtual environment: %s\n' "$python" >&2; exit 1; }
[[ ${EUID} -eq 0 ]] || { printf 'Run manually as root: sudo %s\n' "$0" >&2; exit 1; }

payload_bytes=48000000
while [[ $# -gt 0 ]]; do
  case "$1" in
    --payload-bytes) payload_bytes=$2; shift 2 ;;
    -h|--help) printf 'Usage: %s [--payload-bytes N]\n' "$0"; exit 0 ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done
[[ $payload_bytes =~ ^[1-9][0-9]*$ ]] || { printf '--payload-bytes must be positive\n' >&2; exit 2; }
for ns in moq-p2-relay moq-p2-sub; do
  ip netns list | awk '{print $1}' | grep -Fxq "$ns" || { printf 'Namespace %s is absent\n' "$ns" >&2; exit 1; }
done

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
ip netns exec moq-p2-sub env PYTHONPATH="$root/src" "$python" -m moq_360_protocol_lab.p2_network_sanity \
  --server --address 10.253.2.2 >"$tmp" &
server_pid=$!
sleep 0.2
ip netns exec moq-p2-relay env PYTHONPATH="$root/src" "$python" -m moq_360_protocol_lab.p2_network_sanity \
  --client --address 10.253.2.2 --payload-bytes "$payload_bytes"
wait "$server_pid"
cat "$tmp"
printf 'RTT probe (relay -> subscriber):\n'
ip netns exec moq-p2-relay ping -n -q -c 10 10.253.2.2
