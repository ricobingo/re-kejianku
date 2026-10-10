#!/usr/bin/env bash
# Build the directory uploaded to GitHub Pages (_site/).
#
# The branch itself keeps the lesson source videos. They are not copied into
# the Pages artifact: together they are about 1.27 GiB, and with the HTML the
# package is about 1.44 GB, over the Pages 1 GiB limit.
#
# This script publishes the site files, skips caches and tooling, and writes a
# smaller encode of each lesson video to the same relative path (video/*.mp4)
# so the courseware still plays.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SITE="$ROOT/_site"
# deploy-pages rejects artifacts above 1 GiB (1073741824). Stay under that
# with room for the tar wrapper.
LIMIT=$((1000 * 1024 * 1024))

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg is required to build the Pages site" >&2
  exit 1
fi

rm -rf "$SITE"
mkdir -p "$SITE"

# Courseware only. Source videos, VCS data, this script, and caches stay out.
tar -C "$ROOT" -cf - \
  --exclude='./.git' \
  --exclude='./_site' \
  --exclude='./.github' \
  --exclude='./scripts' \
  --exclude='./node_modules' \
  --exclude='.cache' \
  --exclude='*.mp4' \
  --exclude='*.mov' \
  --exclude='*.mkv' \
  --exclude='*.webm' \
  --exclude='*.log' \
  --exclude='.DS_Store' \
  --exclude='Thumbs.db' \
  . | tar -C "$SITE" -xf -

encode_one() {
  local src="$1"
  local rel="${src#"$ROOT"/}"
  local dest="$SITE/$rel"
  mkdir -p "$(dirname "$dest")"
  ffmpeg -y -hide_banner -loglevel error -i "$src" \
    -map 0:v:0 -map 0:a:0? \
    -c:v libx264 -preset veryfast -profile:v main -crf 26 \
    -maxrate 1200k -bufsize 2400k -pix_fmt yuv420p \
    -threads 2 \
    -c:a aac -b:a 96k -ac 2 \
    -movflags +faststart \
    "$dest"
  echo "encoded ${rel}"
}
export -f encode_one
export ROOT SITE

mapfile -d '' videos < <(find "$ROOT" \
  -path "$ROOT/.git" -prune -o \
  -path "$ROOT/_site" -prune -o \
  -path "$ROOT/scripts" -prune -o \
  -path "$ROOT/.github" -prune -o \
  -type f -iname '*.mp4' -print0)

if ((${#videos[@]} == 0)); then
  echo "no lesson videos found" >&2
  exit 1
fi

echo "Encoding ${#videos[@]} lesson videos. Source masters are not uploaded."
printf '%s\0' "${videos[@]}" | xargs -0 -P 2 -n 1 bash -c 'encode_one "$1"' _

missing=0
for src in "${videos[@]}"; do
  rel="${src#"$ROOT"/}"
  if [[ ! -f "$SITE/$rel" ]]; then
    echo "missing encoded video: $rel" >&2
    missing=1
  fi
done
if ((missing != 0)); then
  exit 1
fi

if [[ ! -f "$SITE/index.html" || ! -f "$SITE/.nojekyll" ]]; then
  echo "site is missing index.html or .nojekyll" >&2
  exit 1
fi

if find "$SITE" -type f \( -iname '*.mov' -o -iname '*.mkv' -o -iname '*.webm' -o -name '.DS_Store' -o -name '*.log' \) | grep -q .; then
  echo "unexpected cache or source media left in _site" >&2
  exit 1
fi

source_bytes=0
for src in "${videos[@]}"; do
  source_bytes=$((source_bytes + $(stat -c '%s' "$src")))
done
site_bytes="$(du -sb "$SITE" | awk '{print $1}')"
video_bytes="$(find "$SITE" -iname '*.mp4' -printf '%s\n' | awk '{s+=$1} END {print s+0}')"

echo "source videos: ${source_bytes} bytes (excluded from the artifact)"
echo "classroom videos in artifact: ${video_bytes} bytes"
echo "Pages payload: ${site_bytes} bytes (limit ${LIMIT})"

if ((site_bytes >= LIMIT)); then
  echo "Pages site is ${site_bytes} bytes, which is not under ${LIMIT}" >&2
  exit 1
fi
