# Strata Supervisor: a web UI on top of the official Strata image. No Strata code is changed.
# The base image is ghcr.io/niko1221/strata:<STRATA_VERSION>-<STRATA_FLAVOR>. The Strata version is set
# only here; CI builds every flavor from it (.github/workflows/build.yml, matrix "flavor").
#   NVIDIA RTX (default): docker build -t strata-supervisor .
#   AMD RX 7000:          docker build --build-arg STRATA_FLAVOR=rocm-gfx110x -t strata-supervisor:rocm .
ARG STRATA_VERSION=0.2.12
ARG STRATA_FLAVOR=cuda13
FROM ghcr.io/niko1221/strata:${STRATA_VERSION}-${STRATA_FLAVOR}

ARG STRATA_VERSION
ARG STRATA_FLAVOR
ARG VERSION=dev
ARG BUILD_DATE=
LABEL org.opencontainers.image.title="Strata Supervisor" \
      org.opencontainers.image.description="Web UI to manage Strata models and load them by hand, one at a time" \
      org.opencontainers.image.source="https://github.com/sognix/strata-supervisor" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      strata.base="ghcr.io/niko1221/strata:${STRATA_VERSION}-${STRATA_FLAVOR}" \
      strata.flavor="${STRATA_FLAVOR}"

# backend/ (Python) and frontend/ (the page) side by side, as in the repo. The license texts travel with the image.
COPY app/ /opt/strata-supervisor/
COPY LICENSE NOTICE.md /opt/strata-supervisor/

# PUID/PGID own the files the supervisor creates in /config (Unraid default nobody:users).
ENV PUID=99 \
    PGID=100 \
    UI_PORT=8081

EXPOSE 8081

# The original check needs a loaded model; this one checks the web UI.
HEALTHCHECK --interval=30s --timeout=8s --start-period=60s --retries=3 \
    CMD ["/opt/strata/terminal/python/bin/python3", "-I", "/opt/strata-supervisor/backend/healthcheck.py"]

ENTRYPOINT ["/usr/bin/tini", "--", "/opt/strata/terminal/python/bin/python3", "/opt/strata-supervisor/backend/main.py"]
CMD []
