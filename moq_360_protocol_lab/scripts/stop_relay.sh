#!/usr/bin/env bash
set -euo pipefail

usage() { printf 'Usage: %s --pid-file PATH [--wait-seconds N]\n' "${0##*/}"; }
pid_file=
wait_seconds=10
while [[ $# -gt 0 ]]; do
  case "$1" in
    --pid-file) pid_file="$2"; shift 2 ;;
    --wait-seconds) wait_seconds="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done
[[ -n "$pid_file" && -f "$pid_file" ]] || { printf 'A valid --pid-file is required\n' >&2; exit 2; }
pid="$(<"$pid_file")"
[[ "$pid" =~ ^[1-9][0-9]*$ ]] || { printf 'Invalid PID file: %s\n' "$pid_file" >&2; exit 1; }
kill -0 "$pid" 2>/dev/null || { printf 'Process %s is not running; preserving PID file for inspection\n' "$pid" >&2; exit 1; }
kill -TERM "$pid"
for ((second=0; second<wait_seconds; second++)); do
  kill -0 "$pid" 2>/dev/null || { rm "$pid_file"; printf 'Stopped relay PID %s\n' "$pid"; exit 0; }
  sleep 1
done
printf 'Relay PID %s did not stop within %s seconds; PID file retained\n' "$pid" "$wait_seconds" >&2
exit 1
