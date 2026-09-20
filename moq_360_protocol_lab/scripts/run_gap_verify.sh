#!/usr/bin/env bash
# Controlled GAP_VERIFY run. Shaping is confined to relay namespace p2rels0.
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
python="$root/.venv/bin/python"
family= name= rep= offered=8
while [[ $# -gt 0 ]]; do
  case "$1" in
    --family) family=$2; shift 2 ;;
    --name) name=$2; shift 2 ;;
    --rep) rep=$2; shift 2 ;;
    --offered-mbps) offered=$2; shift 2 ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done
[[ $EUID -eq 0 ]] || { printf 'Requires root for the existing netns/veth path.\n' >&2; exit 1; }
[[ -x $python && -n $family && -n $name && -n $rep ]] || exit 2
cd "$root"
for ns in moq-p2-pub moq-p2-relay moq-p2-sub; do
  ip netns list | awk '{print $1}' | grep -Fxq "$ns" || { printf 'Missing %s\n' "$ns" >&2; exit 1; }
done
ip -n moq-p2-relay link show p2rels0 >/dev/null
ip netns exec moq-p2-relay ss -lunH | awk '$4 ~ /:4433$/ {found=1} END {exit !found}' || {
  printf 'Pinned relay is not listening in moq-p2-relay.\n' >&2; exit 1;
}
[[ $(ulimit -n) -ge 1024 ]] || { printf 'FD limit below 1024.\n' >&2; exit 1; }
rg -q 'max_bidi_streams: 64' configs/relay.moqx.p2.netns.draft18.yaml || {
  printf 'Expected 64 bidi streams in relay config.\n' >&2; exit 1;
}
available_kb=$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)
[[ $available_kb -ge 1048576 ]] || { printf 'Less than 1 GiB available RAM.\n' >&2; exit 1; }
run_dir=$(PYTHONPATH="$root/src" "$python" -m moq_360_protocol_lab.gap_verify prepare \
  --root "$root/results" --family "$family" --name "$name" --rep "$rep" --offered-mbps "$offered")
printf 'Prepared %s\n' "$run_dir"
printf 'event_type,monotonic_timestamp_ns,track_id,request_id,group_id,object_id,subgroup_id,old_priority,new_priority,scheduler_priority,payload_bytes,observation_point\n' >"$run_dir/relay_scheduler_events.csv"
printf '{"available":false,"reason":"pinned moqx/moxygen build has no GAP_VERIFY scheduler instrumentation; no relay events inferred"}\n' >"$run_dir/instrumentation_status.json"
capacity=$("$python" -c 'import json,sys; print(json.load(open(sys.argv[1]))["capacity_mbps"])' "$run_dir/config.json")
relay_log=/tmp/moq-p2-relay.log
[[ -f $relay_log ]] || { printf 'Relay log unavailable.\n' >&2; exit 1; }
relay_offset=$(wc -c <"$relay_log")
ip netns exec moq-p2-pub ip -j -s link show p2pub0 >"$run_dir/network_publisher_before.json"
ip netns exec moq-p2-relay ip -j -s link show p2rels0 >"$run_dir/network_relay_sub_before.json"
"$root/scripts/apply_p2_shaping.sh" --capacity-mbps "$capacity" --delay-ms 30 >"$run_dir/shaping.log"
pub_pid= sub_pid= monitor_pid=
cleanup() {
  [[ -n $monitor_pid ]] && kill "$monitor_pid" 2>/dev/null || true
  [[ -n $pub_pid ]] && kill "$pub_pid" 2>/dev/null || true
  [[ -n $sub_pid ]] && kill "$sub_pid" 2>/dev/null || true
  "$root/scripts/reset_p2_shaping.sh" >/dev/null 2>&1 || true
}
trap cleanup EXIT
printf 'monotonic_timestamp_ns,relay_alive,relay_cpu_percent,relay_rss_bytes,publisher_alive,publisher_cpu_percent,publisher_rss_bytes,subscriber_alive,subscriber_cpu_percent,subscriber_rss_bytes,publisher_rx_bytes,publisher_tx_bytes,relay_sub_rx_bytes,relay_sub_tx_bytes\n' >"$run_dir/monitor.csv"
stat_pid() {
  local pid=$1
  if [[ -n $pid ]] && ps -p "$pid" -o stat= >/dev/null 2>&1; then
    ps -p "$pid" -o pcpu=,rss= | awk '{printf "1,%s,%s",$1,$2*1024}'
  else printf '0,0,0'; fi
}
iface() {
  ip netns exec "$1" ip -j -s link show dev "$2" | "$python" -c \
    'import json,sys; x=json.load(sys.stdin)[0]["stats64"]; print(x["rx"]["bytes"],x["tx"]["bytes"],sep=",")'
}
monitor() {
  while :; do
    now=$("$python" -c 'import time; print(time.monotonic_ns())')
    relay_pid=$(ip netns exec moq-p2-relay pgrep -x moqx | head -n 1 || true)
    relay_stat=$(stat_pid "$relay_pid")
    pub_stat=$(stat_pid "$pub_pid")
    sub_stat=$(stat_pid "$sub_pid")
    pub_net=$(iface moq-p2-pub p2pub0)
    relay_net=$(iface moq-p2-relay p2rels0)
    printf '%s,%s,%s,%s,%s,%s\n' "$now" "$relay_stat" "$pub_stat" "$sub_stat" "$pub_net" "$relay_net" >>"$run_dir/monitor.csv"
    IFS=, read -r relay_alive relay_cpu relay_rss <<<"$relay_stat"
    IFS=, read -r publisher_alive publisher_cpu publisher_rss <<<"$pub_stat"
    IFS=, read -r subscriber_alive subscriber_cpu subscriber_rss <<<"$sub_stat"
    IFS=, read -r publisher_rx publisher_tx <<<"$pub_net"
    IFS=, read -r relay_rx relay_tx <<<"$relay_net"
    printf '{"monotonic_timestamp_ns":%s,"relay":{"alive":%s,"cpu_percent":%s,"rss_bytes":%s},"publisher":{"alive":%s,"cpu_percent":%s,"rss_bytes":%s},"subscriber":{"alive":%s,"cpu_percent":%s,"rss_bytes":%s},"network":{"publisher_rx_bytes":%s,"publisher_tx_bytes":%s,"relay_sub_rx_bytes":%s,"relay_sub_tx_bytes":%s}}\n' \
      "$now" "$relay_alive" "$relay_cpu" "$relay_rss" "$publisher_alive" "$publisher_cpu" "$publisher_rss" "$subscriber_alive" "$subscriber_cpu" "$subscriber_rss" "$publisher_rx" "$publisher_tx" "$relay_rx" "$relay_tx" >>"$run_dir/monitor.jsonl"
    sleep 0.5
  done
}
monitor & monitor_pid=$!
ip netns exec moq-p2-pub env PYTHONPATH="$root/src" "$python" -m moq_360_protocol_lab.gap_verify \
  endpoint --run-dir "$run_dir" --role publisher >"$run_dir/publisher.stdout.log" 2>&1 & pub_pid=$!
sleep 0.5
ip netns exec moq-p2-sub env PYTHONPATH="$root/src" "$python" -m moq_360_protocol_lab.gap_verify \
  endpoint --run-dir "$run_dir" --role subscriber >"$run_dir/subscriber.stdout.log" 2>&1 & sub_pid=$!
set +e
wait "$pub_pid"; pub_status=$?
wait "$sub_pid"; sub_status=$?
set -e
pub_pid= sub_pid=
kill "$monitor_pid" 2>/dev/null || true
wait "$monitor_pid" 2>/dev/null || true
monitor_pid=
ip netns exec moq-p2-pub ip -j -s link show p2pub0 >"$run_dir/network_publisher_after.json"
ip netns exec moq-p2-relay ip -j -s link show p2rels0 >"$run_dir/network_relay_sub_after.json"
tail -c +$((relay_offset + 1)) "$relay_log" >"$run_dir/relay.log"
relay_end=$(wc -c <"$relay_log")
printf '{"source":"%s","start_offset_bytes":%s,"end_offset_bytes":%s}\n' "$relay_log" "$relay_offset" "$relay_end" >"$run_dir/relay_log_scope.json"
PYTHONPATH="$root/src" "$python" -m moq_360_protocol_lab.gap_verify finalize --run-dir "$run_dir" >"$run_dir/finalize.stdout.log"
PYTHONPATH="$root/src" "$python" -m moq_360_protocol_lab.gap_verify aggregate --root "$root/results"
if [[ -n ${SUDO_USER:-} ]]; then
  chown -R "$SUDO_USER":"$(id -gn "$SUDO_USER")" "$run_dir"
fi
[[ $pub_status -eq 0 && $sub_status -eq 0 && $("$python" -c 'import json,sys; print(int(json.load(open(sys.argv[1]))["valid"]))' "$run_dir/summary.json") -eq 1 ]]
