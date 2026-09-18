#!/usr/bin/env bash
# Create an isolated publisher -> relay -> subscriber test topology for P2.
set -euo pipefail

pub_ns=moq-p2-pub
relay_ns=moq-p2-relay
sub_ns=moq-p2-sub
pub_if=p2pub0
relay_pub_if=p2relp0
relay_sub_if=p2rels0
sub_if=p2sub0

[[ ${EUID} -eq 0 ]] || {
  printf 'Run manually as root: sudo %s\n' "$0" >&2
  exit 1
}

ensure_ns() {
  ip netns list | awk '{print $1}' | grep -Fxq "$1" || ip netns add "$1"
}
for ns in "$pub_ns" "$relay_ns" "$sub_ns"; do ensure_ns "$ns"; done

if ! ip -n "$pub_ns" link show "$pub_if" >/dev/null 2>&1; then
  ip link add "$pub_if" type veth peer name "$relay_pub_if"
  ip link set "$pub_if" netns "$pub_ns"
  ip link set "$relay_pub_if" netns "$relay_ns"
fi
if ! ip -n "$sub_ns" link show "$sub_if" >/dev/null 2>&1; then
  ip link add "$relay_sub_if" type veth peer name "$sub_if"
  ip link set "$relay_sub_if" netns "$relay_ns"
  ip link set "$sub_if" netns "$sub_ns"
fi

for spec in "$pub_ns:$pub_if:10.253.1.2/24" "$relay_ns:$relay_pub_if:10.253.1.1/24" \
            "$relay_ns:$relay_sub_if:10.253.2.1/24" "$sub_ns:$sub_if:10.253.2.2/24"; do
  IFS=: read -r ns dev address <<<"$spec"
  ip -n "$ns" link set lo up
  ip -n "$ns" addr replace "$address" dev "$dev"
  ip -n "$ns" link set "$dev" up
done

printf 'P2 namespaces ready:\n'
printf '  publisher:  %s (%s)\n' "$pub_ns" "10.253.1.2"
printf '  relay:      %s (%s / %s)\n' "$relay_ns" "10.253.1.1" "10.253.2.1"
printf '  subscriber: %s (%s)\n' "$sub_ns" "10.253.2.2"
printf 'Apply shaping only to relay downstream egress with scripts/apply_p2_shaping.sh.\n'
