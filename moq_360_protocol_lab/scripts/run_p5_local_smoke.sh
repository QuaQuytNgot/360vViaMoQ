#!/usr/bin/env bash
# Localhost-only native P5 smoke. Primary measurements use run_p5_experiment.sh.
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
python="$root/.venv/bin/python"
relay_bin="$root/.relay-src/moqx/build/default/moqx"
relay_config="$root/configs/relay.moqx.draft18.example.yaml"
config=
while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) config=$2; shift 2 ;;
    -h|--help) printf 'Usage: %s --config configs/p5.smoke.fetch.local.draft18.yaml\n' "$0"; exit 0 ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done
[[ -n $config && -f $config ]] || { printf -- '--config must name an existing localhost P5 smoke config\n' >&2; exit 2; }
[[ -x $python && -x $relay_bin ]] || { printf 'Missing Python environment or pinned relay binary\n' >&2; exit 1; }
cd "$root"; config=$(readlink -f "$config")
prepared=$(PYTHONPATH="$root/src" "$python" -m moq_360_protocol_lab.p5_runner --prepare --config "$config" --results "$root/results")
run_dir=$(printf '%s' "$prepared" | "$python" -c 'import json,sys; print(json.load(sys.stdin)["run_dir"])')
printf 'Prepared local P5 smoke: %s\n' "$run_dir"

relay_pid= pub_pid= sub_pid= monitor_pid=
cleanup() {
  [[ -n $monitor_pid ]] && kill "$monitor_pid" 2>/dev/null || true
  [[ -n $pub_pid ]] && kill "$pub_pid" 2>/dev/null || true
  [[ -n $sub_pid ]] && kill "$sub_pid" 2>/dev/null || true
  [[ -n $relay_pid ]] && kill -INT "$relay_pid" 2>/dev/null || true
}
trap cleanup EXIT
"$relay_bin" --config "$relay_config" >"$run_dir/relay.log" 2>&1 & relay_pid=$!
for _ in $(seq 1 50); do
  if ss -lunH | awk '$4 ~ /127.0.0.1:4433$/ { found=1 } END { exit !found }'; then break; fi
  sleep 0.1
done
kill -0 "$relay_pid" 2>/dev/null || { printf 'Local relay failed; see %s/relay.log\n' "$run_dir" >&2; exit 1; }
ss -lunH | awk '$4 ~ /127.0.0.1:4433$/ { found=1 } END { exit !found }' || { printf 'Local relay did not listen\n' >&2; exit 1; }
printf '{"source":"%s","start_offset_bytes":0}\n' "$run_dir/relay.log" >"$run_dir/relay_log_scope.json"
ip -j -s link show dev lo >"$run_dir/network_publisher_before.json"
ip -j -s link show dev lo >"$run_dir/network_relay_sub_before.json"
printf 'monotonic_timestamp_ns,relay_alive,relay_cpu_percent,relay_rss_bytes,publisher_alive,publisher_cpu_percent,publisher_rss_bytes,subscriber_alive,subscriber_cpu_percent,subscriber_rss_bytes,publisher_rx_bytes,publisher_tx_bytes,relay_sub_rx_bytes,relay_sub_tx_bytes\n' >"$run_dir/monitor.csv"
proc_stat() { local pid=$1; if [[ -n $pid ]] && ps -p "$pid" -o stat= >/dev/null 2>&1; then ps -p "$pid" -o pcpu=,rss= | awk '{printf "1,%s,%s",$1,$2*1024}'; else printf '0,0,0'; fi; }
lo_bytes() { ip -j -s link show dev lo | "$python" -c 'import json,sys; d=json.load(sys.stdin)[0]["stats64"]; print(d["rx"]["bytes"],d["tx"]["bytes"],sep=",")'; }
monitor() {
  while :; do
    now=$("$python" -c 'import time; print(time.monotonic_ns())')
    relay_stat=$(proc_stat "$relay_pid"); pub_stat=$(proc_stat "$pub_pid"); sub_stat=$(proc_stat "$sub_pid"); net=$(lo_bytes)
    printf '%s,%s,%s,%s,%s\n' "$now" "$relay_stat" "$pub_stat" "$sub_stat" "$net,$net" >>"$run_dir/monitor.csv"
    IFS=, read -r ra rc rr <<<"$relay_stat"; IFS=, read -r pa pc pr <<<"$pub_stat"; IFS=, read -r sa sc sr <<<"$sub_stat"; IFS=, read -r nr nt <<<"$net"
    printf '{"monotonic_timestamp_ns":%s,"relay":{"alive":%s,"cpu_percent":%s,"rss_bytes":%s},"publisher":{"alive":%s,"cpu_percent":%s,"rss_bytes":%s},"subscriber":{"alive":%s,"cpu_percent":%s,"rss_bytes":%s},"network":{"loopback_rx_bytes":%s,"loopback_tx_bytes":%s}}\n' "$now" "$ra" "$rc" "$rr" "$pa" "$pc" "$pr" "$sa" "$sc" "$sr" "$nr" "$nt" >>"$run_dir/monitor.jsonl"
    sleep 0.5
  done
}
PYTHONPATH="$root/src" "$python" -m moq_360_protocol_lab.p5_endpoint --role publisher --run-dir "$run_dir" >"$run_dir/publisher.stdout.log" 2>&1 & pub_pid=$!
sleep 0.5
PYTHONPATH="$root/src" "$python" -m moq_360_protocol_lab.p5_endpoint --role subscriber --run-dir "$run_dir" >"$run_dir/subscriber.stdout.log" 2>&1 & sub_pid=$!
monitor & monitor_pid=$!
set +e
wait "$pub_pid"; pub_status=$?
wait "$sub_pid"; sub_status=$?
set -e
pub_pid=; sub_pid=
kill "$monitor_pid" 2>/dev/null || true; wait "$monitor_pid" 2>/dev/null || true; monitor_pid=
ip -j -s link show dev lo >"$run_dir/network_publisher_after.json"
ip -j -s link show dev lo >"$run_dir/network_relay_sub_after.json"
kill -INT "$relay_pid" 2>/dev/null || true; wait "$relay_pid" 2>/dev/null || true; relay_pid=
trap - EXIT
printf '{"source":"%s","start_offset_bytes":0,"end_offset_bytes":%s}\n' "$run_dir/relay.log" "$(wc -c <"$run_dir/relay.log")" >"$run_dir/relay_log_scope.json"
final_status=0
PYTHONPATH="$root/src" "$python" -m moq_360_protocol_lab.p5_runner --finalize "$run_dir" || final_status=$?
if (( pub_status != 0 || sub_status != 0 )); then
  printf 'P5 local endpoint failure: publisher=%s subscriber=%s (preserved %s)\n' "$pub_status" "$sub_status" "$run_dir" >&2
  exit 1
fi
exit "$final_status"
