#!/bin/sh
# Copy the prepared Electron bundle onto the host's mounted dist/ directory.
set -eu

TARGET_NAME=AudioTrackMerger
DEST="/out/${TARGET_NAME}"
APP_NAME="$(cat /opt/appname)"

if [ ! -d /out ]; then
  echo "Error: /out is not mounted. Use: docker compose --profile desktop run --rm desktop-build" >&2
  exit 1
fi

# only this subdirectory is ours to replace -- never the whole mount
rm -rf "$DEST"
mkdir -p "$DEST"
cp -a /opt/bundle/. "$DEST/"

echo
echo "Desktop bundle is ready:  dist/${TARGET_NAME}/${APP_NAME}.exe"
echo
echo "Run it on the host. The window finds the project folder on its own, starts"
echo "the container if it is not running yet, and opens the UI."
echo
