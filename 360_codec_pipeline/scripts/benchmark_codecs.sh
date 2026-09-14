#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; source "$SCRIPT_DIR/common.sh"
usage() { printf 'Usage: %s --record --codec h264|hevc --encoder NAME --input TILE --output ENCODED --quality-index N [--qp N] [--bitrate RATE] [--preset NAME] [--gop N] [--csv PATH]\nRecords one completed encode; it does not start an experiment.\n' "${0##*/}"; }
record=0; codec=; encoder=; input=; output=; quality=; qp=; bitrate=; preset=; gop=; csv="$PIPELINE_ROOT/results/codec_benchmark.csv"
while [[ $# -gt 0 ]]; do
 case "$1" in
 --record) record=1; shift;; --codec) codec="$2"; shift 2;; --encoder) encoder="$2"; shift 2;; --input) input="$2"; shift 2;; --output) output="$2"; shift 2;; --quality-index) quality="$2"; shift 2;; --qp) qp="$2"; shift 2;; --bitrate) bitrate="$2"; shift 2;; --preset) preset="$2"; shift 2;; --gop) gop="$2"; shift 2;; --csv) csv="$2"; shift 2;; -h|--help) usage; exit 0;; *) die "Unknown or incomplete argument: $1";; esac
done
[[ "$record" -eq 1 ]] || die 'No experiment is launched. Use --record for one completed encode.'
[[ -n "$codec" && -n "$encoder" && -n "$input" && -n "$output" && -n "$quality" ]] || die 'Missing required record fields; see --help'
validate_input_file "$input"; validate_input_file "$output"; ensure_directory "$(dirname "$csv")"
tile_w="$(probe_width "$input")"; tile_h="$(probe_height "$input")"; duration="$(probe_duration "$output")"; size="$(stat -c %s "$output")"
if [[ ! -f "$csv" ]]; then printf 'codec,encoder,tile_resolution,quality_index,qp,bitrate,preset,gop,encode_time_seconds,encode_fps,realtime_factor,decode_fps,file_size_bytes,source_duration_seconds\n' >"$csv"; fi
printf '%s,%s,%sx%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n' "$codec" "$encoder" "$tile_w" "$tile_h" "$quality" "$qp" "$bitrate" "$preset" "$gop" '' '' '' '' "$size" "$duration" >>"$csv"
log_info "Appended benchmark record to $csv; timing/utilisation collection is ready for a future runner."
