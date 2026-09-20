#!/usr/bin/env bash
# Run one native P5 publisher/subscriber pair in the validated netns topology.
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
python="$root/.venv/bin/python"
config=
while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) config=$2; shift 2 ;;
    -h|--help) printf 'Usage: sudo %s --config configs/p5.smoke.fetch.draft18.yaml\n' "$0"; exit 0 ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done
[[ ${EUID} -eq 0 ]] || { printf 'Run manually as root: sudo %s --config ...\n' "$0" >&2; exit 1; }
[[ -n $config && -f $config ]] || { printf -- '--config must name an existing configuration\n' >&2; exit 2; }
[[ -x $python ]] || { printf 'Missing project environment: %s\n' "$python" >&2; exit 1; }
cd "$root"; config=$(readlink -f "$config")
for ns in moq-p2-pub moq-p2-relay moq-p2-sub; do
  ip netns list | awk '{print $1}' | grep -Fxq "$ns" || { printf 'Namespace %s is absent\n' "$ns" >&2; exit 1; }
done
ip netns exec moq-p2-relay ss -lunH | awk '$4 ~ /:4433$/ { found=1 } END { exit !found }' || {
  printf 'No UDP:4433 relay listener. Start scripts/start_p2_relay.sh first.\n' >&2; exit 1;
}

prepared=$(PYTHONPATH="$root/src" "$python" -m moq_360_protocol_lab.p5_runner --prepare --config "$config" --results "$root/results")
run_dir=$(printf '%s' "$prepared" | "$python" -c 'import json,sys; print(json.load(sys.stdin)["run_dir"])')
printf 'Prepared P5 run: %s\n' "$run_dir"

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
  else printf '0,0,0'; fi
}
iface_bytes() { ip netns exec "$1" ip -j -s link show dev "$2" | "$python" -c 'import json,sys; d=json.load(sys.stdin)[0]["stats64"]; print(d["rx"]["bytes"],d["tx"]["bytes"],sep=",")'; }
monitor() {
  while :; do
    now=$("$python" -c 'import time; print(time.monotonic_ns())')
    relay_pid=$(ip netns exec moq-p2-relay pgrep -x moqx | head -n 1 || true)
    relay_stat=$(stat_process "$relay_pid")
    pub_endpoint_pid=$(endpoint_pid moq-p2-pub 'moq_360_protocol_lab.p5_endpoint.*--role publisher')
    sub_endpoint_pid=$(endpoint_pid moq-p2-sub 'moq_360_protocol_lab.p5_endpoint.*--role subscriber')
    pub_stat=$(stat_process_in_netns moq-p2-pub "$pub_endpoint_pid")
    sub_stat=$(stat_process_in_netns moq-p2-sub "$sub_endpoint_pid")
    pub_net=$(iface_bytes moq-p2-pub p2pub0)
    relay_net=$(iface_bytes moq-p2-relay p2rels0)
    printf '%s,%s,%s,%s,%s\n' "$now" "$relay_stat" "$pub_stat" "$sub_stat" "$pub_net,$relay_net" >>"$run_dir/monitor.csv"
    IFS=, read -r relay_alive relay_cpu relay_rss <<<"$relay_stat"
    IFS=, read -r publisher_alive publisher_cpu publisher_rss <<<"$pub_stat"
    IFS=, read -r subscriber_alive subscriber_cpu subscriber_rss <<<"$sub_stat"
    IFS=, read -r publisher_rx publisher_tx <<<"$pub_net"
    IFS=, read -r relay_rx relay_tx <<<"$relay_net"
    printf '{"monotonic_timestamp_ns":%s,"relay":{"alive":%s,"cpu_percent":%s,"rss_bytes":%s},"publisher":{"alive":%s,"cpu_percent":%s,"rss_bytes":%s},"subscriber":{"alive":%s,"cpu_percent":%s,"rss_bytes":%s},"network":{"publisher_rx_bytes":%s,"publisher_tx_bytes":%s,"relay_sub_rx_bytes":%s,"relay_sub_tx_bytes":%s}}\n' \
      "$now" "$relay_alive" "$relay_cpu" "$relay_rss" "$publisher_alive" "$publisher_cpu" "$publisher_rss" \
      "$subscriber_alive" "$subscriber_cpu" "$subscriber_rss" "$publisher_rx" "$publisher_tx" "$relay_rx" "$relay_tx" >>"$run_dir/monitor.jsonl"
    sleep 0.5
  done
}
monitor & monitor_pid=$!
cleanup() { [[ -n $monitor_pid ]] && kill "$monitor_pid" 2>/dev/null || true; [[ -n $pub_pid ]] && kill "$pub_pid" 2>/dev/null || true; [[ -n $sub_pid ]] && kill "$sub_pid" 2>/dev/null || true; }
trap cleanup EXIT

ip netns exec moq-p2-pub env PYTHONPATH="$root/src" "$python" -m moq_360_protocol_lab.p5_endpoint --role publisher --run-dir "$run_dir" >"$run_dir/publisher.stdout.log" 2>&1 & pub_pid=$!
sleep 0.5
ip netns exec moq-p2-sub env PYTHONPATH="$root/src" "$python" -m moq_360_protocol_lab.p5_endpoint --role subscriber --run-dir "$run_dir" >"$run_dir/subscriber.stdout.log" 2>&1 & sub_pid=$!
set +e
wait "$pub_pid"; pub_status=$?
wait "$sub_pid"; sub_status=$?
set -e
pub_pid=; sub_pid=
kill "$monitor_pid" 2>/dev/null || true; wait "$monitor_pid" 2>/dev/null || true; monitor_pid=
trap - EXIT

ip netns exec moq-p2-pub ip -j -s link show dev p2pub0 >"$run_dir/network_publisher_after.json"
ip netns exec moq-p2-relay ip -j -s link show dev p2rels0 >"$run_dir/network_relay_sub_after.json"
relay_end=$(wc -c <"$relay_global")
if (( relay_end >= relay_offset )); then tail -c +$((relay_offset + 1)) "$relay_global" >"$run_dir/relay.log"; else : >"$run_dir/relay.log"; fi
printf '{"source":"%s","start_offset_bytes":%s,"end_offset_bytes":%s}\n' "$relay_global" "$relay_offset" "$relay_end" >"$run_dir/relay_log_scope.json"

final_status=0
PYTHONPATH="$root/src" "$python" -m moq_360_protocol_lab.p5_runner --finalize "$run_dir" || final_status=$?
if [[ -n ${SUDO_USER:-} ]]; then
  chown -R "$SUDO_USER":"$(id -gn "$SUDO_USER")" "$run_dir"
  chown "$SUDO_USER":"$(id -gn "$SUDO_USER")" "$root/results/P5" "$root/results/P5/p5_summary.csv" 2>/dev/null || true
fi
if (( pub_status != 0 || sub_status != 0 )); then
  printf 'P5 endpoint failure: publisher=%s subscriber=%s (preserved %s)\n' "$pub_status" "$sub_status" "$run_dir" >&2
  exit 1
fi
exit "$final_status"
