#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; source "$SCRIPT_DIR/common.sh"
usage() { printf 'Usage: %s --input INPUT --rows N --cols N --output DIR [--gpu]\n' "${0##*/}"; }
input= rows= cols= output= gpu=0
while [[ $# -gt 0 ]]; do case "$1" in
 --input) input="$2"; shift 2;; --rows) rows="$2"; shift 2;; --cols) cols="$2"; shift 2;; --output) output="$2"; shift 2;; --gpu) gpu=1; shift;; -h|--help) usage; exit 0;; *) die "Unknown or incomplete argument: $1";; esac; done
[[ -n "$input" && -n "$rows" && -n "$cols" && -n "$output" ]] || die '--input, --rows, --cols, and --output are required'
validate_input_file "$input"; validate_positive_integer "$rows" rows; validate_positive_integer "$cols" cols; require_command ffmpeg; require_command ffprobe
width="$(probe_width "$input")"; height="$(probe_height "$input")"
(( width % cols == 0 )) || die "Source width $width is not divisible by cols $cols"
(( height % rows == 0 )) || die "Source height $height is not divisible by rows $rows"
tile_w=$((width / cols)); tile_h=$((height / rows)); ensure_directory "$output"
if [[ "$gpu" -eq 1 ]]; then log_warn 'GPU crop path is not selected automatically; using portable CPU crop filters.'; fi
for ((r=0; r<rows; r++)); do for ((c=0; c<cols; c++)); do
 id="$(tile_id "$r" "$c")"; x=$((c * tile_w)); y=$((r * tile_h)); target="$output/$id.mkv"
 log_info "Extracting $id (${tile_w}x${tile_h} at ${x},${y})"
 ffmpeg -y -hide_banner -i "$input" -map 0:v:0 -map 0:a? -vf "crop=${tile_w}:${tile_h}:${x}:${y}" -c:v ffv1 -c:a copy "$target"
done; done
printf 'rows=%s\ncols=%s\ntile_width=%s\ntile_height=%s\n' "$rows" "$cols" "$tile_w" "$tile_h" >"$output/tiles.manifest"
log_info "Created $((rows * cols)) tiles in $output"
