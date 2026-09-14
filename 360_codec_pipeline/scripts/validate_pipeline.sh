#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; source "$SCRIPT_DIR/common.sh"
usage() { printf 'Usage: %s --config FILE [--input FILE]\n' "${0##*/}"; }
config=; input=
while [[ $# -gt 0 ]]; do case "$1" in --config) config="$2"; shift 2;; --input) input="$2"; shift 2;; -h|--help) usage; exit 0;; *) die "Unknown or incomplete argument: $1";; esac; done
[[ -n "$config" ]] || die '--config is required'; load_config "$config"; failures=0
skip() { printf 'SKIPPED: %s\n' "$*"; }; fail() { log_error "$*"; failures=1; }
if [[ -z "${TILE_ROWS:-}" || -z "${TILE_COLS:-}" ]]; then
 skip 'parameter TILE_ROWS/TILE_COLS not configured'
else
 validate_positive_integer "$TILE_ROWS" TILE_ROWS; validate_positive_integer "$TILE_COLS" TILE_COLS
 expected=$((TILE_ROWS * TILE_COLS)); actual="$(find "$PIPELINE_ROOT/media/raw_tiles" -maxdepth 1 -type f -name 'tile_r*_c*.mkv' | wc -l)"
 [[ "$actual" == "$expected" ]] && log_info "PASS: $actual raw tiles" || fail "Expected $expected raw tiles, found $actual"
fi
if [[ -n "$input" ]]; then
 validate_input_file "$input"
 if [[ -n "${TILE_ROWS:-}" && -n "${TILE_COLS:-}" ]]; then w="$(probe_width "$input")"; h="$(probe_height "$input")"; ((w % TILE_COLS == 0 && h % TILE_ROWS == 0)) || fail 'Input dimensions are not divisible by configured tile grid'; fi
else skip 'input not supplied; source dimension validation'; fi
for codec in h264 hevc; do
 if [[ "$codec" == h264 ]]; then list="${H264_QPS:-${H264_BITRATES:-}}"; else list="${HEVC_QPS:-${HEVC_BITRATES:-}}"; fi
 if [[ -z "$list" ]]; then skip "${codec} quality list not configured; encoded-output checks"; continue; fi
 read -r -a levels <<<"$list"
 if [[ -n "${NUM_QUALITIES:-}" ]] && [[ "${#levels[@]}" != "$NUM_QUALITIES" ]]; then fail "$codec quality-list count does not match NUM_QUALITIES"; fi
 for ((q=0; q<${#levels[@]}; q++)); do
  mapfile -t tracks < <(find "$PIPELINE_ROOT/media/encoded/$codec" -path "*/q$q/media.mp4" -type f | sort)
  if [[ -n "${TILE_ROWS:-}" && -n "${TILE_COLS:-}" ]]; then [[ "${#tracks[@]}" == "$((TILE_ROWS*TILE_COLS))" ]] || fail "$codec q$q expected encoded tiles are incomplete"; fi
  [[ -f "$PIPELINE_ROOT/media/reconstructed/${codec}_q${q}.mkv" ]] || fail "Missing reconstruction: ${codec}_q${q}.mkv"
  if ((${#tracks[@]})); then
   gop_args=(); for track in "${tracks[@]}"; do gop_args+=(--input "$track"); done
   "$SCRIPT_DIR/check_gop_alignment.sh" "${gop_args[@]}" || fail "$codec q$q GOP/timestamp alignment failed"
  fi
  decoded_count="$(find "$PIPELINE_ROOT/media/decoded/$codec/q$q" -maxdepth 1 -type f -name 'tile_r*_c*.mkv' 2>/dev/null | wc -l)"
  if [[ -n "${TILE_ROWS:-}" && -n "${TILE_COLS:-}" ]]; then [[ "$decoded_count" == "$((TILE_ROWS*TILE_COLS))" ]] || fail "$codec q$q decoded tile outputs are incomplete"; fi
 done
done
[[ "$failures" -eq 0 ]] && log_info 'PASS: configured pipeline checks completed' || exit 1
