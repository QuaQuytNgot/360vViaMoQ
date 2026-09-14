#!/usr/bin/env bash
# Shared helpers for the 360 codec preprocessing pipeline.
# This file is sourced by other scripts; it is not an entry point.

set -euo pipefail

PIPELINE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

log_info() { printf '[INFO] %s\n' "$*" >&2; }
log_warn() { printf '[WARN] %s\n' "$*" >&2; }
log_error() { printf '[ERROR] %s\n' "$*" >&2; }
die() { log_error "$*"; exit 1; }

require_command() { command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"; }
ensure_directory() { mkdir -p "$1" || die "Unable to create directory: $1"; }
validate_input_file() { [[ -f "$1" ]] || die "Input file does not exist or is not a regular file: $1"; }
validate_positive_integer() { [[ "$1" =~ ^[1-9][0-9]*$ ]] || die "$2 must be a positive integer (got: $1)"; }
validate_nonnegative_integer() { [[ "$1" =~ ^[0-9]+$ ]] || die "$2 must be a non-negative integer (got: $1)"; }

ffprobe_value() {
  local selector="$1" field="$2" input="$3"
  ffprobe -v error -select_streams "$selector" -show_entries "$field" -of default=noprint_wrappers=1:nokey=1 "$input" | head -n1
}
probe_width() { ffprobe_value v:0 stream=width "$1"; }
probe_height() { ffprobe_value v:0 stream=height "$1"; }
probe_fps() {
  local rate
  rate="$(ffprobe_value v:0 stream=avg_frame_rate "$1")"
  awk -F/ 'NF == 2 && $2 != 0 { printf "%.12g", $1/$2; exit } NF == 1 { print; exit }' <<<"$rate"
}
probe_duration() { ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$1" | head -n1; }
probe_frame_count() { ffprobe_value v:0 stream=nb_frames "$1"; }
timestamp_to_seconds() {
  local value="$1"
  awk -v v="$value" 'BEGIN { n=split(v,a,":"); if(n==1) print v; else if(n==2) print a[1]*60+a[2]; else if(n==3) print a[1]*3600+a[2]*60+a[3]; else exit 1 }'
}

load_config() {
  local config_path="$1"
  [[ -n "$config_path" ]] || return 0
  validate_input_file "$config_path"
  # Configuration is intentionally shell syntax: values may be quoted lists.
  # shellcheck disable=SC1090
  source "$config_path"
}

require_config_value() {
  local name="$1" value="${!1:-}"
  [[ -n "$value" ]] || die "Missing $name. Please configure it before running the full pipeline."
}

tile_id() { printf 'tile_r%s_c%s' "$1" "$2"; }
is_approximately_erp() {
  local width="$1" height="$2"
  awk -v w="$width" -v h="$height" 'BEGIN { exit !(h > 0 && (w/h) >= 1.95 && (w/h) <= 2.05) }'
}

encoder_available() { ffmpeg -hide_banner -encoders 2>/dev/null | awk '{print $2}' | grep -Fxq "$1"; }
decoder_available() { ffmpeg -hide_banner -decoders 2>/dev/null | awk '{print $2}' | grep -Fxq "$1"; }

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  printf 'Shared helper library. Source this file from a pipeline script; it has no standalone action.\n'
fi
