#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT/.venv"

usage() {
  printf 'Usage: %s [--venv PATH] [--with-moq-lite]\nInstalls the pinned draft-18 Python environment; it does not install a relay.\n' "${0##*/}"
}

with_lite=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --venv) VENV="$2"; shift 2 ;;
    --with-moq-lite) with_lite=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done

command -v python3 >/dev/null || { printf 'python3 is required\n' >&2; exit 1; }
python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install -r "$ROOT/requirements.txt"
[[ "$with_lite" -eq 1 ]] && "$VENV/bin/python" -m pip install -r "$ROOT/requirements-moq-lite.txt"
printf 'Installed pinned Python dependencies in %s\n' "$VENV"
