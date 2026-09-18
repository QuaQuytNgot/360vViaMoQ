#!/usr/bin/env bash
# Run two real P2 endpoints in their separate namespaces and finalize evidence.
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
python="$root/.venv/bin/python"
cd "$root"
config=
while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) config=$2; shift 2 ;;
    -h|--help) printf 'Usage: sudo %s --config configs/p2.static.equal.draft18.yaml\n' "$0"; exit 0 ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done
[[ ${EUID} -eq 0 ]] || { printf 'Run manually as root: sudo %s --config ...\n' "$0" >&2; exit 1; }
[[ -n $config && -f $config ]] || { printf '--config must name an existing configuration\n' >&2; exit 2; }
config=$(readlink -f "$config")
[[ -x $python ]] || { printf 'Missing project environment: %s\n' "$python" >&2; exit 1; }
for ns in moq-p2-pub moq-p2-relay moq-p2-sub; do
  ip netns list | awk '{print $1}' | grep -Fxq "$ns" || { printf 'Namespace %s is absent\n' "$ns" >&2; exit 1; }
done
ip netns exec moq-p2-relay ss -lunH | awk '$4 ~ /:4433$/ { found=1 } END { exit !found }' || {
  printf 'No UDP:4433 relay listener in moq-p2-relay. Start scripts/start_p2_relay.sh in another terminal.\n' >&2; exit 1;
}

prepared=$(PYTHONPATH="$root/src" "$python" -m moq_360_protocol_lab.p2_runner --prepare --config "$config" --results "$root/results")
run_dir=$(printf '%s' "$prepared" | "$python" -c 'import json,sys; print(json.load(sys.stdin)["run_dir"])')
printf 'Prepared P2 run: %s\n' "$run_dir"
pub_pid=
sub_pid=
cleanup() {
  [[ -n $pub_pid ]] && kill "$pub_pid" 2>/dev/null || true
  [[ -n $sub_pid ]] && kill "$sub_pid" 2>/dev/null || true
}
trap cleanup EXIT
ip netns exec moq-p2-pub env PYTHONPATH="$root/src" "$python" -m moq_360_protocol_lab.p2_endpoint \
  --role publisher --run-dir "$run_dir" >"$run_dir/publisher.stdout.log" 2>&1 &
pub_pid=$!
# PUBLISH is announced before SUBSCRIBE.  Object generation still waits for
# the relay's Forward signal, so this short sequencing delay does not prefill
# the shaped path.
sleep 0.5
ip netns exec moq-p2-sub env PYTHONPATH="$root/src" "$python" -m moq_360_protocol_lab.p2_endpoint \
  --role subscriber --run-dir "$run_dir" >"$run_dir/subscriber.stdout.log" 2>&1 &
sub_pid=$!
wait "$pub_pid"
wait "$sub_pid"
pub_pid=
sub_pid=
trap - EXIT
PYTHONPATH="$root/src" "$python" -m moq_360_protocol_lab.p2_runner --finalize "$run_dir"
# Namespace processes may map root-owned files to nobody on this host.  Return
# completed evidence to the invoking researcher so later analysis never needs
# sudo.  Do this only after all finalizer writes have completed.
if [[ -n ${SUDO_USER:-} ]]; then
  chown -R "$SUDO_USER":"$(id -gn "$SUDO_USER")" "$run_dir"
fi
