#!/usr/bin/env bash
# Local dev server (no Docker). Docker users just run: docker compose up
set -euo pipefail
cd "$(dirname "$0")"

if [ -z "${FFMPEG_BIN:-}" ] && ! command -v ffmpeg >/dev/null 2>&1; then
  WIN_FF=$(ls -d "$LOCALAPPDATA/Microsoft/WinGet/Packages/Gyan.FFmpeg"*/*/bin 2>/dev/null | head -1 || true)
  if [ -n "$WIN_FF" ]; then
    export FFMPEG_BIN="$WIN_FF/ffmpeg.exe"
    export FFPROBE_BIN="$WIN_FF/ffprobe.exe"
  fi
fi

export MEDIA_DIR="${MEDIA_DIR:-$PWD/media}"
export WORK_DIR="${WORK_DIR:-$PWD/work}"

exec .venv/Scripts/python.exe -m uvicorn backend.app.main:app \
  --host 127.0.0.1 --port 5174
