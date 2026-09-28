#!/bin/sh
# Copy the prepared Electron bundle onto the host's mounted dist/ directory.
set -eu

TARGET_NAME=HelireaUhitaja
DEST="/out/${TARGET_NAME}"
APP_NAME="$(cat /opt/appname)"

if [ ! -d /out ]; then
  echo "Viga: /out ei ole ühendatud. Kasuta: docker compose --profile desktop run --rm desktop-build" >&2
  exit 1
fi

# only this subdirectory is ours to replace -- never the whole mount
rm -rf "$DEST"
mkdir -p "$DEST"
cp -a /opt/bundle/. "$DEST/"

echo
echo "Töölauapakk on valmis:  dist/${TARGET_NAME}/${APP_NAME}.exe"
echo
echo "Käivita see hostis. Aken otsib ise projektikausta üles, käivitab"
echo "konteineri kui see veel ei jookse, ja avab kasutajaliidese."
echo
