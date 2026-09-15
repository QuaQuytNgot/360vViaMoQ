#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPOSITORY="https://github.com/openmoq/moqx.git"
COMMIT="502b6b8f9ddbf4e61f5efe41e92a3d1df40df3a6"
SOURCE_DIR="$ROOT/.relay-src/moqx"
MOXYGEN_MODE="prebuilt-with-fallback"
apply=0

usage() {
  printf 'Usage: %s [--source-dir PATH] [--moxygen MODE] [--apply]\n\nPins the audited moqx candidate at %s. Without --apply it prints the build steps. This candidate is not selected for draft-18 claims until its local protocol and P1 media-path smokes pass.\n' "${0##*/}" "$COMMIT"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source-dir) SOURCE_DIR="$2"; shift 2 ;;
    --moxygen) MOXYGEN_MODE="$2"; shift 2 ;;
    --apply) apply=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

case "$MOXYGEN_MODE" in prebuilt|prebuilt-with-fallback|from-source) ;; *) printf 'Invalid --moxygen mode: %s\n' "$MOXYGEN_MODE" >&2; exit 2 ;; esac

commands=(
  "git clone $REPOSITORY $SOURCE_DIR"
  "git -C $SOURCE_DIR checkout --detach $COMMIT"
  "$SOURCE_DIR/scripts/configure.sh default --moxygen $MOXYGEN_MODE"
  "$SOURCE_DIR/scripts/build.sh default"
  "$SOURCE_DIR/build/default/moqx validate-config --config $ROOT/configs/relay.moqx.draft18.example.yaml"
)
printf 'Pinned relay candidate: moqx %s\n' "$COMMIT"
printf 'Planned commands:\n'; printf '  %s\n' "${commands[@]}"
[[ "$apply" -eq 1 ]] || exit 0

command -v git >/dev/null || { printf 'git is required\n' >&2; exit 1; }
if [[ ! -d "$SOURCE_DIR/.git" ]]; then
  mkdir -p "$(dirname "$SOURCE_DIR")"
  git clone "$REPOSITORY" "$SOURCE_DIR"
fi
git -C "$SOURCE_DIR" fetch --tags origin
git -C "$SOURCE_DIR" checkout --detach "$COMMIT"
actual_commit="$(git -C "$SOURCE_DIR" rev-parse HEAD)"
[[ "$actual_commit" == "$COMMIT" ]] || { printf 'Checked out %s, expected %s\n' "$actual_commit" "$COMMIT" >&2; exit 1; }
"$SOURCE_DIR/scripts/configure.sh" default --moxygen "$MOXYGEN_MODE"
"$SOURCE_DIR/scripts/build.sh" default
printf 'Built moqx candidate at %s\nRemote: %s\nCommit: %s\nMoxygen mode: %s\n' "$SOURCE_DIR" "$(git -C "$SOURCE_DIR" remote get-url origin)" "$actual_commit" "$MOXYGEN_MODE"
