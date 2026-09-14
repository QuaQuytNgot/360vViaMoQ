#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; source "$SCRIPT_DIR/common.sh"
usage() { printf 'Usage: %s --config FILE [--input FILE]\nRuns the configured preprocessing pipeline. No experimental defaults are applied.\n' "${0##*/}"; }
config=; input=
while [[ $# -gt 0 ]]; do case "$1" in --config) config="$2"; shift 2;; --input) input="$2"; shift 2;; -h|--help) usage; exit 0;; *) die "Unknown or incomplete argument: $1";; esac; done
[[ -n "$config" ]] || die 'Missing --config. Copy configs/pipeline.env.example and provide experiment parameters.'
load_config "$config"; input="${input:-${INPUT_VIDEO:-}}"
for setting in TILE_ROWS TILE_COLS GOP_SIZE B_FRAMES H264_PRESET HEVC_PRESET H264_RC HEVC_RC FRAGMENT_DURATION; do require_config_value "$setting"; done
[[ -n "$input" ]] || die 'Missing input video (--input or INPUT_VIDEO).'; validate_input_file "$input"
[[ -n "${H264_QPS:-}" || -n "${H264_BITRATES:-}" ]] || die 'Missing H264_QPS or H264_BITRATES.'
[[ -n "${HEVC_QPS:-}" || -n "${HEVC_BITRATES:-}" ]] || die 'Missing HEVC_QPS or HEVC_BITRATES.'
if [[ -n "${NUM_QUALITIES:-}" ]]; then validate_positive_integer "$NUM_QUALITIES" NUM_QUALITIES; fi
"$SCRIPT_DIR/probe_video.sh" "$input"
"$SCRIPT_DIR/tile_erp.sh" --input "$input" --rows "$TILE_ROWS" --cols "$TILE_COLS" --output "$PIPELINE_ROOT/media/raw_tiles"
for codec in h264 hevc; do
 if [[ "$codec" == h264 ]]; then
  qps="${H264_QPS:-}"; rates="${H264_BITRATES:-}"; preset="$H264_PRESET"; rc="$H264_RC"; impl="${H264_ENCODER:-nvenc}"; encoder_script="$SCRIPT_DIR/encode_h264.sh"
 else
  qps="${HEVC_QPS:-}"; rates="${HEVC_BITRATES:-}"; preset="$HEVC_PRESET"; rc="$HEVC_RC"; impl="${HEVC_ENCODER:-nvenc}"; encoder_script="$SCRIPT_DIR/encode_hevc.sh"
 fi
 values="$qps"; mode=qp
 [[ -n "$values" ]] || { values="$rates"; mode=bitrate; }
 read -r -a levels <<<"$values"
 [[ -z "${NUM_QUALITIES:-}" || "${#levels[@]}" == "$NUM_QUALITIES" ]] || die "$codec quality list count does not match NUM_QUALITIES"
 for tile in "$PIPELINE_ROOT"/media/raw_tiles/tile_r*_c*.mkv; do
  [[ -f "$tile" ]] || die 'No raw tiles found'; tile_name="$(basename "${tile%.mkv}")"
  for ((q=0; q<${#levels[@]}; q++)); do
   track="$PIPELINE_ROOT/media/encoded/$codec/$tile_name/q$q"; encoded="$track/media.mp4"; ensure_directory "$track"
   "$encoder_script" --input "$tile" --output "$encoded" --"$mode" "${levels[q]}" --gop "$GOP_SIZE" --preset "$preset" --rc "$rc" --bframes "$B_FRAMES" --encoder "$impl" --quality-index "$q"
   fragment_dir="$PIPELINE_ROOT/media/fragmented/$codec/$tile_name/q$q"
   "$SCRIPT_DIR/package_fragments.sh" --input "$encoded" --output "$fragment_dir" --fragment-duration "$FRAGMENT_DURATION"
   decoded_dir="$PIPELINE_ROOT/media/decoded/$codec/q$q"; ensure_directory "$decoded_dir"
   "$SCRIPT_DIR/decode_tiles.sh" --codec "$codec" --input "$encoded" --output "$decoded_dir/$tile_name.mkv" --decoder auto
  done
 done
 for ((q=0; q<${#levels[@]}; q++)); do
  "$SCRIPT_DIR/reconstruct_erp.sh" --input-dir "$PIPELINE_ROOT/media/decoded/$codec/q$q" --rows "$TILE_ROWS" --cols "$TILE_COLS" --output "$PIPELINE_ROOT/media/reconstructed/${codec}_q${q}.mkv"
 done
done
"$SCRIPT_DIR/validate_pipeline.sh" --config "$config" --input "$input"
