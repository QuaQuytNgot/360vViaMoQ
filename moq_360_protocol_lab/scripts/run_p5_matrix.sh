#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
[[ ${EUID} -eq 0 ]] || { printf 'Run manually as root: sudo %s --series P5A\n' "$0" >&2; exit 1; }
exec env PYTHONPATH="$root/src" "$root/.venv/bin/python" -m moq_360_protocol_lab.p5_matrix "$@"

