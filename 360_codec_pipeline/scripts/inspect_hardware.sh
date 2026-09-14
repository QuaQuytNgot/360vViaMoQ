#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

usage() { printf 'Usage: %s [--output PATH]\n' "${0##*/}"; }
output="$PIPELINE_ROOT/results/hardware_capabilities.txt"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --output) output="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown argument: $1" ;;
  esac
done
ensure_directory "$(dirname "$output")"
{
  printf 'Generated: %s\n\n' "$(date -Is)"
  printf '== OS ==\n'; (cat /etc/os-release 2>/dev/null || uname -a)
  printf '\n== Architecture ==\n'; uname -m
  printf '\n== CPU ==\n'; (lscpu 2>/dev/null || true)
  printf '\n== RAM ==\n'; (free -h 2>/dev/null || true)
  printf '\n== NVIDIA GPU / driver / CUDA ==\n'; (command -v nvidia-smi >/dev/null && nvidia-smi 2>&1) || printf 'nvidia-smi unavailable or driver not operational\n'
  if command -v ffmpeg >/dev/null 2>&1; then
    printf '\n== FFmpeg version ==\n'; ffmpeg -version 2>&1
    printf '\n== FFmpeg hardware accelerations ==\n'; ffmpeg -hide_banner -hwaccels 2>&1
    printf '\n== NVENC encoders ==\n'; ffmpeg -hide_banner -encoders 2>&1 | grep -E 'h264_nvenc|hevc_nvenc' || printf 'No h264_nvenc or hevc_nvenc encoder found\n'
    printf '\n== Available hardware decoders ==\n'; ffmpeg -hide_banner -decoders 2>&1 | grep -Ei 'cuvid|cuda|nvdec|qsv|vaapi|videotoolbox|d3d11' || printf 'No recognized hardware decoder found\n'
    printf '\n== h264_nvenc options ==\n'; ffmpeg -hide_banner -h encoder=h264_nvenc 2>&1 || true
    printf '\n== hevc_nvenc options ==\n'; ffmpeg -hide_banner -h encoder=hevc_nvenc 2>&1 || true
  else
    printf '\n== FFmpeg ==\nffmpeg unavailable; cannot inspect build, accelerators, encoders, or decoders\n'
  fi
} >"$output"
log_info "Hardware report written to $output"
