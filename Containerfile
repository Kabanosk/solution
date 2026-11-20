# Backend image: API, the three MCP servers, agent worker and data tools in one image.
# Build from the repo root:  docker build -f Containerfile -t cfo-backend .
# Entry: `cfo <command>` (see deploy/cfo-entrypoint.sh); default CMD is `cfo api`.
FROM registry.access.redhat.com/ubi9/python-312:latest

USER 0

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH=/opt/venv/bin:$PATH \
    HOME=/app

RUN pip install --no-cache-dir "uv>=0.5,<1"

WORKDIR /app

# Dependencies first (cached unless pyproject.toml or uv.lock change).
COPY backend/pyproject.toml backend/uv.lock backend/.python-version /app/backend/
RUN uv sync --project /app/backend --frozen --no-dev --no-install-project --no-editable

# Project code.
COPY backend/cfo /app/backend/cfo
RUN uv sync --project /app/backend --frozen --no-dev --no-editable \
    && rm -rf /app/backend/cfo

COPY deploy/cfo-entrypoint.sh /usr/local/bin/cfo

# OpenShift runs the container with an arbitrary UID in group 0: everything the app
# writes to (HOME, data/, workstation files) must be group-0 writable.
RUN chmod 0755 /usr/local/bin/cfo \
    && mkdir -p /app/data \
    && chgrp -R 0 /app /opt/venv \
    && chmod -R g=u /app /opt/venv

USER 1001
WORKDIR /app

EXPOSE 8000 8001 8002 8003

ENTRYPOINT ["cfo"]
CMD ["api"]
