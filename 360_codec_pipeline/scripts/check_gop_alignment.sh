#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; source "$SCRIPT_DIR/common.sh"
usage() { printf 'Usage: %s --input FILE [--input FILE ...] [--tolerance SECONDS]\n' "${0##*/}"; }
declare -a inputs=(); tolerance=0.001
while [[ $# -gt 0 ]]; do case "$1" in --input) inputs+=("$2"); shift 2;; --tolerance) tolerance="$2"; shift 2;; -h|--help) usage; exit 0;; *) die "Unknown or incomplete argument: $1";; esac; done
((${#inputs[@]} >= 1)) || die 'At least one --input is required'; require_command ffprobe
for file in "${inputs[@]}"; do validate_input_file "$file"; done
key_timestamps() { ffprobe -v error -select_streams v:0 -show_frames -show_entries frame=key_frame,best_effort_timestamp_time -of csv=p=0 "$1" | awk -F, '$1==1 {print $2}'; }
frame_timestamps() { ffprobe -v error -select_streams v:0 -show_frames -show_entries frame=best_effort_timestamp_time -of csv=p=0 "$1"; }
declare -a reference=(); mapfile -t reference < <(key_timestamps "${inputs[0]}")
((${#reference[@]})) || die "No keyframes found in ${inputs[0]}"
declare -a reference_frames=(); mapfile -t reference_frames < <(frame_timestamps "${inputs[0]}")
((${#reference_frames[@]})) || die "No frame timestamps found in ${inputs[0]}"
status=0; log_info "Reference ${inputs[0]} has ${#reference[@]} random-access points"
for file in "${inputs[@]:1}"; do
 declare -a current=(); mapfile -t current < <(key_timestamps "$file")
 matched=1
 if ((${#current[@]} != ${#reference[@]})); then log_error "FAIL $file: keyframe count ${#current[@]} != ${#reference[@]}"; status=1; matched=0; fi
 for ((i=0; i<${#reference[@]}; i++)); do
   [[ "$matched" -eq 1 ]] || break
   if ! awk -v a="${reference[i]}" -v b="${current[i]}" -v t="$tolerance" 'BEGIN {exit !(a-b<=t && b-a<=t)}'; then log_error "FAIL $file: keyframe $i (${current[i]}) differs from ${reference[i]}"; status=1; matched=0; break; fi
 done
 if [[ "$matched" -eq 1 ]]; then
  declare -a current_frames=(); mapfile -t current_frames < <(frame_timestamps "$file")
  if ((${#current_frames[@]} != ${#reference_frames[@]})); then
   log_error "FAIL $file: frame timestamp count ${#current_frames[@]} != ${#reference_frames[@]}"; status=1; matched=0
  else
   for ((i=0; i<${#reference_frames[@]}; i++)); do
    if ! awk -v a="${reference_frames[i]}" -v b="${current_frames[i]}" -v t="$tolerance" 'BEGIN {exit !(a-b<=t && b-a<=t)}'; then log_error "FAIL $file: frame timestamp $i (${current_frames[i]}) differs from ${reference_frames[i]}"; status=1; matched=0; break; fi
   done
  fi
 fi
 [[ "$matched" -eq 1 ]] && log_info "PASS $file: keyframe and frame timestamps align"
done
[[ "$status" -eq 0 ]] && log_info 'PASS: GOP/keyframe alignment verified' || exit 1
