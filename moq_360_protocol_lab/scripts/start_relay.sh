#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'Usage: %s --pid-file PATH --log-file PATH --binary MOQX --config RELAY_YAML\n\nStarts the audited moqx candidate with its documented `moqx --config FILE` interface. The configuration must force `moqt_versions: [18]`; run scripts/probe_relay.sh before a live experiment.\n' "${0##*/}"
}

pid_file=
log_file=
binary=
config=
while [[ $# -gt 0 ]]; do
  case "$1" in
    --pid-file) pid_file="$2"; shift 2 ;;
    --log-file) log_file="$2"; shift 2 ;;
    --binary) binary="$2"; shift 2 ;;
    --config) config="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done
[[ -n "$pid_file" && -n "$log_file" && -n "$binary" && -n "$config" ]] || { usage >&2; exit 2; }
[[ -x "$binary" ]] || { printf 'Relay binary is not executable: %s\n' "$binary" >&2; exit 1; }
[[ -f "$config" ]] || { printf 'Relay config does not exist: %s\n' "$config" >&2; exit 1; }
[[ ! -e "$pid_file" ]] || { printf 'PID file already exists: %s\n' "$pid_file" >&2; exit 1; }
mkdir -p "$(dirname "$pid_file")" "$(dirname "$log_file")"
# `setsid` keeps the intentional relay daemon alive when this launcher is run
# from a non-interactive harness whose shell process group is cleaned up at
# command completion.  `stop_relay.sh` still owns the PID-file shutdown path.
setsid "$binary" --config "$config" >"$log_file" 2>&1 < /dev/null &
printf '%s\n' "$!" >"$pid_file"
printf 'Started relay PID %s; log: %s\n' "$(<"$pid_file")" "$log_file"
