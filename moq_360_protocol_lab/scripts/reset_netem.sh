#!/usr/bin/env bash
set -euo pipefail

usage() { printf 'Usage: %s --interface IFACE [--apply]\nWithout --apply this only shows the removal command.\n' "${0##*/}"; }
interface=
apply=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --interface) interface="$2"; shift 2 ;;
    --apply) apply=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done
[[ -n "$interface" ]] || { usage >&2; exit 2; }
printf 'Planned command: tc qdisc del dev %s root\n' "$interface"
[[ "$apply" -eq 1 ]] || exit 0
command -v tc >/dev/null || { printf 'tc is required\n' >&2; exit 1; }
tc qdisc del dev "$interface" root
printf 'Removed root qdisc from %s\n' "$interface"
