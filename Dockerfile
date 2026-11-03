# syntax=docker/dockerfile:1.7
#
# Mercalli self-hosted server.
#
#   docker build -t mercalli .                           # API, web app (when web/ has one) and ML
#   docker build -t mercalli --build-arg EXTRAS=server .  # leaner: no forecasting libraries
#   docker build -t mercalli --build-arg BUILD_WEB=skip . # API only

ARG PYTHON_VERSION=3.12
ARG NODE_VERSION=24
ARG UV_VERSION=0.9.5

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# Stage 1: the Python environment, built with uv into /opt/venv.
FROM python:${PYTHON_VERSION}-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
ARG EXTRAS=ml,server
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /src
RUN uv venv /opt/venv
# Dependencies first and on their own, so a code change does not reinstall them.
COPY pyproject.toml README.md ./
RUN --mount=type=cache,id=mercalli-uv,target=/root/.cache/uv \
    uv pip compile pyproject.toml $(printf -- '--extra %s ' $(echo "$EXTRAS" | tr ',' ' ')) \
      --quiet -o /tmp/requirements.txt \
 && uv pip install --python /opt/venv/bin/python -r /tmp/requirements.txt
COPY src ./src
COPY catalog ./catalog
# The test suites that numpy, scipy, pyarrow and friends ship are never imported at runtime.
RUN --mount=type=cache,id=mercalli-uv,target=/root/.cache/uv \
    uv pip install --python /opt/venv/bin/python --no-deps . \
 && find /opt/venv/lib -type d -name tests -path "*/site-packages/*/*" -prune -exec rm -rf {} +
# The web app pre-renders one page per service, so its build needs the service list. Taking it
# from the catalog baked into this image keeps those pages and the server's catalog in step.
RUN /opt/venv/bin/mercalli catalog export-services --out /web-data/services.json

# Stage 2: the web app, exported as static files. The web app lives in web/ and may be absent
# from a checkout, so the stage looks for web/package.json through a bind mount of the build
# context (a COPY of a missing path would fail the build) and leaves /out empty without it.
FROM node:${NODE_VERSION}-slim AS web
ARG BUILD_WEB=auto
ENV NEXT_TELEMETRY_DISABLED=1 \
    NEXT_PUBLIC_BASE_PATH="" \
    MERCALLI_SERVICES_JSON=/web-data/services.json \
    COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
    CI=1
COPY --from=build /web-data/services.json /web-data/services.json
WORKDIR /web
RUN --mount=type=bind,source=.,target=/context \
    --mount=type=cache,id=mercalli-pnpm,target=/pnpm-store \
    set -eu; \
    mkdir -p /out; \
    if [ "$BUILD_WEB" = "skip" ] || [ ! -f /context/web/package.json ]; then \
      echo "No web app to build: the image will serve the API only."; \
      exit 0; \
    fi; \
    cp -a /context/web/. /web/; \
    corepack enable pnpm; \
    if [ -f pnpm-lock.yaml ]; then \
      pnpm install --frozen-lockfile --store-dir /pnpm-store; \
    else \
      pnpm install --store-dir /pnpm-store; \
    fi; \
    pnpm run build; \
    if [ ! -f out/index.html ]; then \
      echo "pnpm run build wrote no web/out/index.html; the app needs output: 'export'" >&2; \
      exit 1; \
    fi; \
    cp -a out/. /out/

# Stage 3: the runtime image. No compilers, no package managers, no root.
FROM python:${PYTHON_VERSION}-slim
LABEL org.opencontainers.image.title="Mercalli" \
      org.opencontainers.image.description="Self-hosted outage intelligence for the services you depend on" \
      org.opencontainers.image.source="https://github.com/dharmeshdev1/mercalli" \
      org.opencontainers.image.licenses="Apache-2.0"
RUN groupadd --system --gid 10001 mercalli \
 && useradd --system --uid 10001 --gid mercalli --home-dir /var/lib/mercalli --shell /usr/sbin/nologin mercalli \
 && install -d -o mercalli -g mercalli /var/lib/mercalli /var/lib/mercalli/cache
COPY --from=build /opt/venv /opt/venv
COPY --from=web /out /srv/mercalli/web
COPY LICENSE NOTICE /usr/share/doc/mercalli/
# Libraries that cache downloads (embedding models, for one) are pointed at the cache directory,
# which stays writable when the root filesystem is read only.
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MERCALLI_HOST=0.0.0.0 \
    MERCALLI_PORT=8080 \
    MERCALLI_DB="sqlite:////var/lib/mercalli/mercalli.db" \
    MERCALLI_WEB=/srv/mercalli/web \
    MERCALLI_CACHE=/var/lib/mercalli/cache \
    XDG_CACHE_HOME=/var/lib/mercalli/cache \
    HF_HOME=/var/lib/mercalli/cache/huggingface
USER 10001:10001
WORKDIR /var/lib/mercalli
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('MERCALLI_PORT', '8080'), timeout=4)"]
ENTRYPOINT ["mercalli"]
CMD ["serve"]
