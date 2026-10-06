# syntax=docker/dockerfile:1
#
# AgentLab: the API, the web interface and the worker in one image.
#
#   docker build -f docker/Dockerfile -t agentlab .
#   docker run --rm -p 127.0.0.1:8080:8080 -e AGENTLAB_API_TOKEN="$(openssl rand -hex 24)" agentlab
#
# The image has no Docker client and no browser. Without a Docker client the sandbox is unavailable, so tests that would
# run untrusted code are BLOCKED with that reason (they are never run in the container itself); without a browser,
# browser tests are BLOCKED. See docs/installation.md#docker.
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
RUN /opt/venv/bin/pip install ".[anthropic,redis,postgres,mcp]"

# ---- what runs ----------------------------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    AGENTLAB_CONFIG=/etc/agentlab/agentlab.yaml
RUN useradd --system --uid 10001 --create-home --home-dir /home/agentlab agentlab \
    && mkdir -p /data /etc/agentlab \
    && chown agentlab /data
COPY --from=build /opt/venv /opt/venv
COPY docker/agentlab.yaml /etc/agentlab/agentlab.yaml
USER agentlab
WORKDIR /data
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8080/health', timeout=4)"]
ENTRYPOINT ["agentlab"]
CMD ["serve"]
