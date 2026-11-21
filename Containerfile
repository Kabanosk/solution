# Backend image: API, the three MCP servers, agent worker and data tools in one image.
# Build from the repo root:  docker build -f Containerfile -t cfo-backend .
# Entry: `cfo <command>` (see deploy/cfo-entrypoint.sh); default CMD is `cfo api`.
ARG DEPENDENCIES_IMAGE=cfo-backend-dependencies:latest
FROM ${DEPENDENCIES_IMAGE}

USER 0
WORKDIR /app

COPY backend/pyproject.toml backend/uv.lock backend/.python-version /app/backend/

COPY backend/cfo /app/backend/cfo
COPY backend/scout /app/backend/scout
RUN uv sync --project /app/backend --frozen --no-dev --extra mlflow --extra tuning --no-editable \
    && rm -rf /app/backend/cfo /app/backend/scout

COPY deploy/cfo-entrypoint.sh /usr/local/bin/cfo

# OpenShift runs the container with an arbitrary UID in group 0: everything the app
# writes to (HOME, data/, workstation files) must be group-0 writable.
RUN chmod 0755 /usr/local/bin/cfo \
    && mkdir -p /app/data \
    && chgrp -R 0 /app \
    && chmod -R g=u /app

USER 1001
WORKDIR /app

EXPOSE 8000 8001 8002 8003

ENTRYPOINT ["cfo"]
CMD ["api"]
