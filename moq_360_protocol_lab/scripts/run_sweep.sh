#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
usage() { printf 'Usage: %s CONFIG.yaml [CONFIG.yaml ...]\nRuns explicitly supplied configurations only; matrix expansion is not implicit.\n' "${0##*/}"; }
[[ "${1:-}" != "-h" && "${1:-}" != "--help" ]] || { usage; exit 0; }
[[ $# -gt 0 ]] || { usage >&2; exit 2; }
for config in "$@"; do
  [[ -f "$config" ]] || { printf 'Configuration not found: %s\n' "$config" >&2; exit 1; }
  "$ROOT/scripts/run_test.sh" --config "$config"
done
