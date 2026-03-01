#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <youtube_url> [sub_langs] [output_dir]" >&2
  echo "Example: $0 'https://www.youtube.com/watch?v=EdcNB7GXH2c' 'ko' '/tmp'" >&2
  exit 2
fi

url="$1"
sub_langs="${2:-ko}"
output_dir="${3:-/tmp}"

# Prefer the user-site install path that worked in this workspace.
if [[ -x "$HOME/Library/Python/3.9/bin/yt-dlp" ]]; then
  ytdlp_bin="$HOME/Library/Python/3.9/bin/yt-dlp"
elif command -v yt-dlp >/dev/null 2>&1; then
  ytdlp_bin="$(command -v yt-dlp)"
else
  echo "yt-dlp not found." >&2
  echo "Install with: python3 -m pip install --user yt-dlp" >&2
  exit 127
fi

mkdir -p "$output_dir"

# Stable profile from successful runs:
# - use android+web client to avoid common subtitle misses
# - request one language first (default: ko) to reduce HTTP 429 on translation tracks
"$ytdlp_bin" -v \
  --skip-download \
  --write-auto-subs \
  --sub-langs "$sub_langs" \
  --sub-format vtt \
  --extractor-args "youtube:player_client=android,web" \
  -o "$output_dir/yt-%(id)s.%(ext)s" \
  "$url"

echo
echo "Saved subtitle files:"
ls -1 "$output_dir"/yt-*.vtt 2>/dev/null | tail -n 20 || true
