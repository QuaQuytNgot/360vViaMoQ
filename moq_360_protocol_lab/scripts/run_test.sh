#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT/.venv/bin/python"
[[ -x "$PYTHON" ]] || PYTHON=python3

usage() { printf 'Usage: %s --config FILE [--capabilities FILE] [--execute-plan]\n' "${0##*/}"; }
config=
capabilities="$ROOT/configs/capabilities.example.json"
execute=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) config="$2"; shift 2 ;;
    --capabilities) capabilities="$2"; shift 2 ;;
    --execute-plan) execute=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done
[[ -n "$config" ]] || { usage >&2; exit 2; }
args=(--config "$config" --capabilities "$capabilities" --results "$ROOT/results")
[[ "$execute" -eq 1 ]] && args+=(--execute-plan)
cd "$ROOT"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON" -m moq_360_protocol_lab.experiment "${args[@]}"
