#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; source "$SCRIPT_DIR/common.sh"
usage() { printf 'Usage: %s --codec h264|hevc --input FILE --output FILE --decoder auto|gpu|cpu\n' "${0##*/}"; }
codec= input= output= decoder=auto
while [[ $# -gt 0 ]]; do case "$1" in --codec) codec="$2"; shift 2;; --input) input="$2"; shift 2;; --output) output="$2"; shift 2;; --decoder) decoder="$2"; shift 2;; -h|--help) usage; exit 0;; *) die "Unknown or incomplete argument: $1";; esac; done
[[ -n "$codec" && -n "$input" && -n "$output" ]] || die '--codec, --input, and --output are required'; [[ "$codec" == h264 || "$codec" == hevc ]] || die '--codec must be h264 or hevc'; [[ "$decoder" == auto || "$decoder" == gpu || "$decoder" == cpu ]] || die '--decoder must be auto, gpu, or cpu'; validate_input_file "$input"; require_command ffmpeg; ensure_directory "$(dirname "$output")"
cuvid="${codec}_cuvid"; args=(-y -hide_banner)
if [[ "$decoder" != cpu ]] && decoder_available "$cuvid"; then
  log_info "Decoding with $cuvid"; args+=(-c:v "$cuvid")
elif [[ "$decoder" == gpu ]]; then
  die "GPU decoder $cuvid is unavailable; use --decoder cpu or auto"
else
  log_info 'Decoding with FFmpeg software decoder'
fi
args+=(-i "$input" -map 0:v:0 -c:v ffv1 "$output")
ffmpeg "${args[@]}"
