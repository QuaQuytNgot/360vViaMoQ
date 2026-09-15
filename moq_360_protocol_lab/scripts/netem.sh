#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'Usage: %s --interface IFACE [--bandwidth-mbps N] [--delay-ms N] [--jitter-ms N] [--loss-percent N] [--queue-limit N] [--apply]\n\nWithout --apply this prints the exact tc commands. Delay is one-way outbound delay; do not label it RTT unless both directions are shaped and that setup is recorded.\n' "${0##*/}"
}

interface=
bandwidth=
delay=
jitter=
loss=
limit=
apply=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --interface) interface="$2"; shift 2 ;;
    --bandwidth-mbps) bandwidth="$2"; shift 2 ;;
    --delay-ms) delay="$2"; shift 2 ;;
    --jitter-ms) jitter="$2"; shift 2 ;;
    --loss-percent) loss="$2"; shift 2 ;;
    --queue-limit) limit="$2"; shift 2 ;;
    --apply) apply=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done

[[ -n "$interface" ]] || { usage >&2; exit 2; }
[[ -n "$bandwidth$delay$jitter$loss$limit" ]] || { printf 'Provide at least one network condition\n' >&2; exit 2; }
[[ -z "$jitter" || -n "$delay" ]] || { printf -- '--jitter-ms requires --delay-ms\n' >&2; exit 2; }
[[ -z "$bandwidth" || "$bandwidth" =~ ^[0-9]+([.][0-9]+)?$ ]] || { printf 'Invalid bandwidth: %s\n' "$bandwidth" >&2; exit 2; }
for value in "$delay" "$jitter" "$loss"; do [[ -z "$value" || "$value" =~ ^[0-9]+([.][0-9]+)?$ ]] || { printf 'Invalid numeric condition: %s\n' "$value" >&2; exit 2; }; done
[[ -z "$limit" || "$limit" =~ ^[1-9][0-9]*$ ]] || { printf 'queue limit must be a positive integer\n' >&2; exit 2; }

netem_args=()
[[ -n "$delay" ]] && netem_args+=(delay "${delay}ms")
[[ -n "$jitter" ]] && netem_args+=("${jitter}ms")
[[ -n "$loss" ]] && netem_args+=(loss "${loss}%")
[[ -n "$limit" ]] && netem_args+=(limit "$limit")

if [[ -n "$bandwidth" ]]; then
  commands=(
    "tc qdisc replace dev $interface root handle 1: htb default 1"
    "tc class replace dev $interface parent 1: classid 1:1 htb rate ${bandwidth}mbit"
  )
  [[ ${#netem_args[@]} -gt 0 ]] && commands+=("tc qdisc replace dev $interface parent 1:1 handle 10: netem ${netem_args[*]}")
else
  commands=("tc qdisc replace dev $interface root handle 10: netem ${netem_args[*]}")
fi
printf 'Planned commands:\n'; printf '  %s\n' "${commands[@]}"
[[ "$apply" -eq 1 ]] || exit 0
command -v tc >/dev/null || { printf 'tc is required\n' >&2; exit 1; }
if [[ -n "$bandwidth" ]]; then
  tc qdisc replace dev "$interface" root handle 1: htb default 1
  tc class replace dev "$interface" parent 1: classid 1:1 htb rate "${bandwidth}mbit"
  [[ ${#netem_args[@]} -gt 0 ]] && tc qdisc replace dev "$interface" parent 1:1 handle 10: netem "${netem_args[@]}"
else
  tc qdisc replace dev "$interface" root handle 10: netem "${netem_args[@]}"
fi
printf 'Applied outbound qdisc(s) to %s. Save these exact conditions in run provenance.\n' "$interface"
