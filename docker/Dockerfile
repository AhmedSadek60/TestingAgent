# syntax=docker/dockerfile:1
#
# AgentLab: the API, the web interface and the worker in one image.
#
#   docker build -f docker/Dockerfile -t agentlab .
#   docker run --rm -p 127.0.0.1:8080:8080 -e AGENTLAB_API_TOKEN="$(openssl rand -hex 24)" agentlab
#
# The image has git (to clone a repository by URL), but no Docker client, and no browser unless built with WITH_BROWSER=1. Without a Docker client the sandbox is unavailable, so tests that would
# run untrusted code are BLOCKED with that reason (they are never run in the container itself); without a browser,
# browser tests are BLOCKED. See docs/installation.md#docker.
#
# Railway builds this file too (railway.json, docs/deployment-railway.md). Its builder refuses the VOLUME instruction, so
# there is none: Compose and Railway both mount their own volume at /data.
ARG NODE_IMAGE=node:22-slim
ARG PYTHON_IMAGE=python:3.12-slim

# ---- the web interface ------------------------------------------------------------------------------------------
FROM ${NODE_IMAGE} AS web
WORKDIR /repo/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
# Type-checks, then writes the built interface to ../src/agentlab/api/static (see vite.config.ts).
RUN npm run build

# ---- the Python package, with the interface inside it ------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /repo
RUN python -m venv /opt/venv
COPY pyproject.toml README.md ./
COPY src ./src
COPY --from=web /repo/src/agentlab/api/static ./src/agentlab/api/static
ARG WITH_BROWSER=0
RUN /opt/venv/bin/pip install ".[anthropic,redis,postgres,mcp]" \
    && if [ "$WITH_BROWSER" = "1" ]; then /opt/venv/bin/pip install ".[browser]"; fi

# ---- what runs ----------------------------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    AGENTLAB_CONFIG=/etc/agentlab/agentlab.yaml
# git clones a repository given by URL (src/agentlab/repository/ingest.py). It is only ever used to clone, with hooks and
# every protocol but HTTPS switched off; nothing from a cloned repository runs in this container (it needs the sandbox).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --system --uid 10001 --create-home --home-dir /home/agentlab agentlab \
    && mkdir -p /data /etc/agentlab \
    && chown agentlab /data
COPY --from=build /opt/venv /opt/venv
# Chromium for browser tests, only when asked for (--build-arg WITH_BROWSER=1; docker/compose.vps.yaml does). Chromium keeps its
# own sandbox, which needs user namespaces: the container must then run with docker/seccomp-chromium.json (Docker's default
# profile plus clone, clone3, unshare and setns), see docs/deployment-vps.md. Without WITH_BROWSER the image has no browser.
ARG WITH_BROWSER=0
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
RUN if [ "$WITH_BROWSER" = "1" ]; then /opt/venv/bin/playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*; fi
COPY docker/agentlab.yaml /etc/agentlab/agentlab.yaml
COPY docker/agentlab.railway.yaml /etc/agentlab/railway.yaml
COPY --chmod=0755 docker/railway-start.sh /usr/local/bin/agentlab-railway-start
USER agentlab
WORKDIR /data
EXPOSE 8080
# The port is 8080 unless a platform injects PORT, which docker/railway-start.sh then listens on.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request as u; u.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '8080'), timeout=4)"]
ENTRYPOINT ["agentlab"]
CMD ["serve"]
