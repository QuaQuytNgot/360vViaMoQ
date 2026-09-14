#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; source "$SCRIPT_DIR/common.sh"
usage() { printf 'Usage: %s --input FILE --output DIR --fragment-duration SECONDS [--playlist NAME]\n' "${0##*/}"; }
input= output= duration= playlist=manifest.m3u8
while [[ $# -gt 0 ]]; do case "$1" in --input) input="$2"; shift 2;; --output) output="$2"; shift 2;; --fragment-duration) duration="$2"; shift 2;; --playlist) playlist="$2"; shift 2;; -h|--help) usage; exit 0;; *) die "Unknown or incomplete argument: $1";; esac; done
[[ -n "$input" && -n "$output" && -n "$duration" ]] || die '--input, --output, and --fragment-duration are required'; validate_input_file "$input"; require_command ffmpeg; ensure_directory "$output"
ffmpeg -y -hide_banner -i "$input" -map 0 -c copy -f hls -hls_time "$duration" -hls_playlist_type vod -hls_segment_type fmp4 -hls_fmp4_init_filename init.mp4 -hls_segment_filename "$output/group_%06d.m4s" -hls_flags independent_segments "$output/$playlist"
log_info "Wrote init.mp4 and keyframe-bounded fMP4 fragments to $output"
