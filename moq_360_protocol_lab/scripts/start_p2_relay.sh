#!/usr/bin/env bash
# Start the pinned strict draft-18 relay inside the isolated P2 relay namespace.
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
relay_bin="$root/.relay-src/moqx/build/default/moqx"
relay_config="$root/configs/relay.moqx.p2.netns.draft18.yaml"
log=/tmp/moq-p2-relay.log

[[ ${EUID} -eq 0 ]] || { printf 'Run manually as root: sudo %s\n' "$0" >&2; exit 1; }
[[ -x $relay_bin ]] || { printf 'Pinned moqx binary is missing: %s\n' "$relay_bin" >&2; exit 1; }
ip netns list | awk '{print $1}' | grep -Fxq moq-p2-relay || { printf 'Namespace moq-p2-relay is absent\n' >&2; exit 1; }
# ``ss`` prints Local Address:Port in column 5 only when its State column is
# omitted; with ``-H`` on iproute2 it is column 4.  Match the local column,
# not the peer address (which is normally ``*`` for UDP).
if ip netns exec moq-p2-relay ss -lunH | awk '$4 ~ /:4433$/ { found=1 } END { exit !found }'; then
  printf 'A UDP listener already uses port 4433 in moq-p2-relay; refusing to start a second relay.\n' >&2
  exit 1
fi
exec ip netns exec moq-p2-relay "$relay_bin" --config "$relay_config" >"$log" 2>&1
