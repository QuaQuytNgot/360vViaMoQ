#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; source "$SCRIPT_DIR/common.sh"
usage() { printf 'Usage: %s INPUT [--format text|shell|json] [--strict-erp]\n' "${0##*/}"; }
format=text strict=0 input=
while [[ $# -gt 0 ]]; do
  case "$1" in
    --format) format="$2"; shift 2 ;; --strict-erp) strict=1; shift ;;
    -h|--help) usage; exit 0 ;; -*) die "Unknown argument: $1" ;;
    *) [[ -z "$input" ]] || die 'Only one input may be supplied'; input="$1"; shift ;;
  esac
done
[[ -n "$input" ]] || die 'INPUT is required'; validate_input_file "$input"; require_command ffprobe
codec="$(ffprobe_value v:0 stream=codec_name "$input")"; width="$(probe_width "$input")"; height="$(probe_height "$input")"
fps="$(probe_fps "$input")"; duration="$(probe_duration "$input")"; pix_fmt="$(ffprobe_value v:0 stream=pix_fmt "$input")"; frames="$(probe_frame_count "$input")"
aspect="$(awk -v w="$width" -v h="$height" 'BEGIN {if(h) printf "%.8g",w/h}')"; erp=no; is_approximately_erp "$width" "$height" && erp=yes
[[ "$strict" -eq 0 || "$erp" == yes ]] || die "Input is not approximately 2:1 ERP (observed aspect ratio: $aspect)"
case "$format" in
 text) printf 'codec=%s\nwidth=%s\nheight=%s\naspect_ratio=%s\nfps=%s\nduration_seconds=%s\npixel_format=%s\nframe_count=%s\napproximately_erp_2_to_1=%s\n' "$codec" "$width" "$height" "$aspect" "$fps" "$duration" "$pix_fmt" "${frames:-unavailable}" "$erp" ;;
 shell) printf 'VIDEO_CODEC=%q\nVIDEO_WIDTH=%q\nVIDEO_HEIGHT=%q\nVIDEO_ASPECT_RATIO=%q\nVIDEO_FPS=%q\nVIDEO_DURATION=%q\nVIDEO_PIXEL_FORMAT=%q\nVIDEO_FRAME_COUNT=%q\nVIDEO_APPROXIMATELY_ERP=%q\n' "$codec" "$width" "$height" "$aspect" "$fps" "$duration" "$pix_fmt" "${frames:-}" "$erp" ;;
 json) printf '{"codec":"%s","width":%s,"height":%s,"aspect_ratio":%s,"fps":%s,"duration_seconds":%s,"pixel_format":"%s","frame_count":"%s","approximately_erp_2_to_1":%s}\n' "$codec" "$width" "$height" "$aspect" "$fps" "$duration" "$pix_fmt" "${frames:-}" "$erp" ;;
 *) die '--format must be text, shell, or json' ;;
esac
