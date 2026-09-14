#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; source "$SCRIPT_DIR/common.sh"
usage() { printf 'Usage: %s --input FILE --output FILE (--qp N | --bitrate RATE) --gop N --preset NAME --rc MODE --bframes N [--fps FPS] [--encoder nvenc|cpu] [--quality-index N]\n' "${0##*/}"; }
input= output= qp= bitrate= gop= preset= rc= bframes= fps= encoder=nvenc quality_index=
while [[ $# -gt 0 ]]; do case "$1" in
 --input) input="$2"; shift 2;; --output) output="$2"; shift 2;; --qp) qp="$2"; shift 2;; --bitrate) bitrate="$2"; shift 2;; --gop) gop="$2"; shift 2;; --preset) preset="$2"; shift 2;; --rc) rc="$2"; shift 2;; --bframes) bframes="$2"; shift 2;; --fps) fps="$2"; shift 2;; --encoder) encoder="$2"; shift 2;; --quality-index) quality_index="$2"; shift 2;; -h|--help) usage; exit 0;; *) die "Unknown or incomplete argument: $1";; esac; done
[[ -n "$input" && -n "$output" && -n "$gop" && -n "$preset" && -n "$rc" && -n "$bframes" ]] || die 'Missing required encoding arguments; see --help'
[[ -n "$qp" || -n "$bitrate" ]] || die 'Provide --qp and/or --bitrate'; validate_input_file "$input"; validate_positive_integer "$gop" gop; validate_nonnegative_integer "$bframes" bframes; require_command ffmpeg
[[ "$encoder" == nvenc || "$encoder" == cpu ]] || die '--encoder must be nvenc or cpu'; ensure_directory "$(dirname "$output")"
args=(-y -hide_banner -i "$input" -map 0:v:0 -map 0:a? -g "$gop" -bf "$bframes")
[[ -n "$fps" ]] && args+=(-r "$fps")
if [[ "$encoder" == nvenc ]]; then
 encoder_available hevc_nvenc || die 'hevc_nvenc is unavailable; run inspect_hardware.sh or use --encoder cpu'
 args+=(-c:v hevc_nvenc -preset "$preset" -rc "$rc")
 [[ -n "$qp" ]] && args+=(-qp "$qp"); [[ -n "$bitrate" ]] && args+=(-b:v "$bitrate")
else
 encoder_available libx265 || die 'libx265 is unavailable in this FFmpeg build'
 args+=(-c:v libx265 -preset "$preset")
 [[ -n "$qp" ]] && args+=(-x265-params "qp=$qp"); [[ -n "$bitrate" ]] && args+=(-b:v "$bitrate")
fi
args+=(-c:a copy "$output"); log_info "Encoding HEVC with $encoder"; ffmpeg "${args[@]}"
printf 'codec=hevc\nencoder=%s\nquality_index=%s\nqp=%s\nbitrate=%s\ngop=%s\npreset=%s\nrc=%s\nbframes=%s\nfps=%s\n' "$encoder" "$quality_index" "$qp" "$bitrate" "$gop" "$preset" "$rc" "$bframes" "$fps" >"${output%.*}.metadata"
