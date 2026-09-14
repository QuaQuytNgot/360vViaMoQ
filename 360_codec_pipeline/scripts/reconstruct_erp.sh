#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; source "$SCRIPT_DIR/common.sh"
usage() { printf 'Usage: %s --input-dir DIR --rows N --cols N --output FILE\nExpected files: tile_rR_cC.(mkv|mp4|mov|webm)\n' "${0##*/}"; }
input_dir= rows= cols= output=
while [[ $# -gt 0 ]]; do case "$1" in --input-dir) input_dir="$2"; shift 2;; --rows) rows="$2"; shift 2;; --cols) cols="$2"; shift 2;; --output) output="$2"; shift 2;; -h|--help) usage; exit 0;; *) die "Unknown or incomplete argument: $1";; esac; done
[[ -n "$input_dir" && -n "$rows" && -n "$cols" && -n "$output" ]] || die '--input-dir, --rows, --cols, and --output are required'; [[ -d "$input_dir" ]] || die "Input directory not found: $input_dir"; validate_positive_integer "$rows" rows; validate_positive_integer "$cols" cols; require_command ffmpeg; require_command ffprobe; ensure_directory "$(dirname "$output")"
declare -a inputs=(); tile_w= tile_h= expected_frames=
for ((r=0; r<rows; r++)); do for ((c=0; c<cols; c++)); do
 id="$(tile_id "$r" "$c")"; file=
 for candidate in "$input_dir/$id".{mkv,mp4,mov,webm}; do [[ -f "$candidate" ]] && { file="$candidate"; break; }; done
 [[ -n "$file" ]] || die "Missing decoded tile: $id in $input_dir"
 w="$(probe_width "$file")"; h="$(probe_height "$file")"; frames="$(probe_frame_count "$file")"
 [[ -z "$tile_w" ]] && { tile_w="$w"; tile_h="$h"; expected_frames="$frames"; }
 [[ "$w" == "$tile_w" && "$h" == "$tile_h" ]] || die "Tile $id dimensions ${w}x${h} differ from ${tile_w}x${tile_h}"
 [[ -z "$expected_frames" || -z "$frames" || "$frames" == "$expected_frames" ]] || die "Tile $id frame count $frames differs from $expected_frames"
 inputs+=("$file")
done; done
filter=; input_index=0
for ((r=0; r<rows; r++)); do
 row_inputs=; for ((c=0; c<cols; c++)); do row_inputs+="[$input_index:v]"; ((input_index+=1)); done
 filter+="${row_inputs}hstack=inputs=${cols}[row${r}];"
done
stack_inputs=; for ((r=0; r<rows; r++)); do stack_inputs+="[row${r}]"; done
filter+="${stack_inputs}vstack=inputs=${rows}[outv]"
cmd=(ffmpeg -y -hide_banner); for file in "${inputs[@]}"; do cmd+=(-i "$file"); done
cmd+=(-filter_complex "$filter" -map '[outv]' -c:v ffv1 "$output"); ffmpeg "${cmd[@]}"
actual_w="$(probe_width "$output")"; actual_h="$(probe_height "$output")"; [[ "$actual_w" == "$((tile_w * cols))" && "$actual_h" == "$((tile_h * rows))" ]] || die "Reconstruction dimension validation failed: got ${actual_w}x${actual_h}"
log_info "Reconstructed ERP ${actual_w}x${actual_h}: $output"
