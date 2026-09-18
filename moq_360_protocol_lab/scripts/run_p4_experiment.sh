#!/usr/bin/env bash
# Run a native P4 endpoint pair in the existing isolated P2 topology.
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
python="$root/.venv/bin/python"
config=
while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) config=$2; shift 2 ;;
    -h|--help) printf 'Usage: sudo %s --config configs/p4.forward-smoke.0-to-1.draft18.yaml\n' "$0"; exit 0 ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done
[[ ${EUID} -eq 0 ]] || { printf 'Run manually as root: sudo %s --config ...\n' "$0" >&2; exit 1; }
[[ -n $config && -f $config ]] || { printf '--config must name an existing configuration\n' >&2; exit 2; }
[[ -x $python ]] || { printf 'Missing project environment: %s\n' "$python" >&2; exit 1; }
cd "$root"; config=$(readlink -f "$config")
for ns in moq-p2-pub moq-p2-relay moq-p2-sub; do
  ip netns list | awk '{print $1}' | grep -Fxq "$ns" || { printf 'Namespace %s is absent\n' "$ns" >&2; exit 1; }
done
ip netns exec moq-p2-relay ss -lunH | awk '$4 ~ /:4433$/ { found=1 } END { exit !found }' || {
  printf 'No UDP:4433 relay listener. Start scripts/start_p2_relay.sh first.\n' >&2; exit 1;
}
prepared=$(PYTHONPATH="$root/src" "$python" -m moq_360_protocol_lab.p4_runner --prepare --config "$config" --results "$root/results")
run_dir=$(printf '%s' "$prepared" | "$python" -c 'import json,sys; print(json.load(sys.stdin)["run_dir"])')
printf 'Prepared P4 run: %s\n' "$run_dir"
phase() {
  PHASE_NAME=$1 PHASE_ROLE=$2 RUN_DIR=$run_dir "$python" -c 'import json,os,time; p=os.path.join(os.environ["RUN_DIR"],"run_phases.events.jsonl"); d={"event_type":"run_phase","observed_ts_ns":time.monotonic_ns(),"clock_domain_id":"local-monotonic","run_id":os.path.basename(os.environ["RUN_DIR"]),"provenance":"p4_harness_lifecycle","native_operation":os.environ["PHASE_NAME"],"details":{"phase":os.environ["PHASE_NAME"],"role":os.environ["PHASE_ROLE"]}}; open(p,"a").write(json.dumps(d,separators=(",",":"))+"\n")'
}
phase RUN_START orchestrator
# Scope the relay evidence by byte offsets, retaining only bytes written while
# this run is active. This avoids changing relay policy or restarting it.
relay_global=/tmp/moq-p2-relay.log
relay_offset=$(wc -c <"$relay_global")
printf '{"source":"%s","start_offset_bytes":%s}\n' "$relay_global" "$relay_offset" >"$run_dir/relay_log_scope.json"
ip netns exec moq-p2-pub ip -j -s link show dev p2pub0 >"$run_dir/network_publisher_before.json"
ip netns exec moq-p2-relay ip -j -s link show dev p2rels0 >"$run_dir/network_relay_sub_before.json"
pub_pid= sub_pid= monitor_pid=
printf 'monotonic_timestamp_ns,relay_alive,relay_cpu_percent,relay_rss_bytes,publisher_alive,publisher_cpu_percent,publisher_rss_bytes,subscriber_alive,subscriber_cpu_percent,subscriber_rss_bytes,publisher_rx_bytes,publisher_tx_bytes,relay_sub_rx_bytes,relay_sub_tx_bytes\n' >"$run_dir/monitor.csv"
stat_process() { local pid=$1; if [[ -n $pid ]] && ps -p "$pid" -o stat= >/dev/null 2>&1; then ps -p "$pid" -o pcpu=,rss= | awk '{printf "1,%s,%s",$1,$2*1024}'; else printf '0,0,0'; fi; }
endpoint_pid() { ip netns exec "$1" pgrep -f "$2" | head -n 1 || true; }
stat_process_in_netns() {
  local ns=$1 pid=$2
  if [[ -n $pid ]] && ip netns exec "$ns" ps -p "$pid" -o stat= >/dev/null 2>&1; then
    ip netns exec "$ns" ps -p "$pid" -o pcpu=,rss= | awk '{printf "1,%s,%s",$1,$2*1024}'
  else
    printf '0,0,0'
  fi
}
iface_bytes() { ip netns exec "$1" ip -j -s link show dev "$2" | "$python" -c 'import json,sys; d=json.load(sys.stdin)[0]["stats64"]; print(d["rx"]["bytes"],d["tx"]["bytes"],sep=",")'; }
monitor() {
  while :; do
    local_now=$("$python" -c 'import time; print(time.monotonic_ns())')
    relay_pid=$(ip netns exec moq-p2-relay pgrep -x moqx | head -n 1 || true)
    relay_stat=$(stat_process "$relay_pid")
    # The background ip-netns-exec wrapper can exit or re-parent before its
    # endpoint process does. Resolve the endpoint inside each namespace so
    # liveness and resource sampling describe the actual Python process.
    pub_endpoint_pid=$(endpoint_pid moq-p2-pub 'moq_360_protocol_lab.p4_endpoint.*--role publisher')
    sub_endpoint_pid=$(endpoint_pid moq-p2-sub 'moq_360_protocol_lab.p4_endpoint.*--role subscriber')
    pub_stat=$(stat_process_in_netns moq-p2-pub "$pub_endpoint_pid")
    sub_stat=$(stat_process_in_netns moq-p2-sub "$sub_endpoint_pid")
    pub_net=$(iface_bytes moq-p2-pub p2pub0)
    relay_net=$(iface_bytes moq-p2-relay p2rels0)
    printf '%s,%s,%s,%s,%s\n' "$local_now" "$relay_stat" "$pub_stat" "$sub_stat" "$pub_net,$relay_net" >>"$run_dir/monitor.csv"
    sleep 0.5
  done
}
monitor & monitor_pid=$!
cleanup() { [[ -n $monitor_pid ]] && kill "$monitor_pid" 2>/dev/null || true; [[ -n $pub_pid ]] && kill "$pub_pid" 2>/dev/null || true; [[ -n $sub_pid ]] && kill "$sub_pid" 2>/dev/null || true; }
trap cleanup EXIT
ip netns exec moq-p2-pub env PYTHONPATH="$root/src" "$python" -m moq_360_protocol_lab.p4_endpoint --role publisher --run-dir "$run_dir" >"$run_dir/publisher.stdout.log" 2>&1 & pub_pid=$!
sleep 0.5
ip netns exec moq-p2-sub env PYTHONPATH="$root/src" "$python" -m moq_360_protocol_lab.p4_endpoint --role subscriber --run-dir "$run_dir" >"$run_dir/subscriber.stdout.log" 2>&1 & sub_pid=$!
wait "$pub_pid"; wait "$sub_pid"; pub_pid=; sub_pid=; trap - EXIT
phase ENDPOINT_TEARDOWN_COMPLETE orchestrator
kill "$monitor_pid" 2>/dev/null || true; wait "$monitor_pid" 2>/dev/null || true; monitor_pid=
ip netns exec moq-p2-pub ip -j -s link show dev p2pub0 >"$run_dir/network_publisher_after.json"
ip netns exec moq-p2-relay ip -j -s link show dev p2rels0 >"$run_dir/network_relay_sub_after.json"
relay_end=$(wc -c <"$relay_global")
if (( relay_end >= relay_offset )); then tail -c +$((relay_offset + 1)) "$relay_global" >"$run_dir/relay.log"; else : >"$run_dir/relay.log"; fi
printf '{"source":"%s","start_offset_bytes":%s,"end_offset_bytes":%s}\n' "$relay_global" "$relay_offset" "$relay_end" >"$run_dir/relay_log_scope.json"
phase RUN_END orchestrator
PYTHONPATH="$root/src" "$python" -m moq_360_protocol_lab.p4_runner --finalize "$run_dir"
if [[ -n ${SUDO_USER:-} ]]; then
  # New raw run ownership plus writable aggregate/analysis directory; do not
  # alter prior run contents.
  chown -R "$SUDO_USER":"$(id -gn "$SUDO_USER")" "$run_dir"
  chown "$SUDO_USER":"$(id -gn "$SUDO_USER")" "$root/results/P4"
fi
