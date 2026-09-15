#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT/.venv/bin/python"
[[ -x "$PYTHON" ]] || PYTHON=python3

usage() { printf 'Usage: %s --config FILE\nProbes only the configured backend and aborts on any non-draft-18/raw-QUIC/moqt-18 negotiation.\n' "${0##*/}"; }
config=
while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) config="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done
[[ -n "$config" ]] || { usage >&2; exit 2; }
cd "$ROOT"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON" -m moq_360_protocol_lab.protocol_probe --config "$config"
