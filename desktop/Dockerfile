# Builds the desktop bundle without needing Node, npm or Electron on the host.
#
# Electron ships prebuilt binaries for every platform, and an app is just
# `resources/app/` dropped next to one of them -- no compiler, no packager, no
# native modules here. So this image only fetches the archive for the target
# platform and lays the two app files inside it, which also means the host's
# Node version stops mattering entirely.

FROM debian:bookworm-slim AS bundle

# keep in step with the electron devDependency in desktop/package.json,
# which is what `npm start` from source uses
ARG ELECTRON_VERSION=33.2.0
ARG ELECTRON_PLATFORM=win32-x64
ARG APP_NAME=Helirea-uhitaja

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl unzip \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build

RUN curl -fsSL --retry 3 -o electron.zip \
      "https://github.com/electron/electron/releases/download/v${ELECTRON_VERSION}/electron-v${ELECTRON_VERSION}-${ELECTRON_PLATFORM}.zip" \
 && mkdir -p bundle \
 && unzip -q electron.zip -d bundle \
 && rm electron.zip \
 && mkdir -p bundle/resources/app

COPY desktop/main.js desktop/package.json /build/bundle/resources/app/

# the launcher keeps the product name; Electron is happy to be renamed
RUN if [ -f bundle/electron.exe ]; then mv bundle/electron.exe "bundle/${APP_NAME}.exe"; \
    elif [ -f bundle/electron ]; then mv bundle/electron "bundle/${APP_NAME}"; fi \
 && printf '%s\n' "${APP_NAME}" > /build/appname


FROM debian:bookworm-slim

COPY --from=bundle /build/bundle /opt/bundle
COPY --from=bundle /build/appname /opt/appname
COPY desktop/install-bundle.sh /usr/local/bin/install-bundle

RUN chmod +x /usr/local/bin/install-bundle

# /out is the host's dist/ directory
VOLUME ["/out"]
CMD ["/usr/local/bin/install-bundle"]
