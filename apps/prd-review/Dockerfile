# syntax=docker/dockerfile:1
FROM node:24-bookworm-slim AS dsh-build
ENV COREPACK_HOME=/opt/corepack CI=true
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential ca-certificates git musl-tools \
    && rm -rf /var/lib/apt/lists/*
COPY . .
ARG DSH_CLIENT_COMMIT_HASH=0000000
RUN --mount=type=cache,id=prd-dsh-pnpm,target=/pnpm/store,sharing=locked \
    corepack pnpm install --frozen-lockfile --store-dir /pnpm/store
RUN corepack pnpm --dir native/system run build:native \
    && DSH_GATE_CONCURRENCY=1 corepack pnpm run build:artifacts
RUN --mount=type=cache,id=prd-assets-npm,target=/root/.npm,sharing=locked \
    npm ci --prefix apps/prd-review/assets --ignore-scripts --no-audit --no-fund \
    && node apps/prd-review/assets/node_modules/@larksuite/cli/scripts/install.js \
    && apps/prd-review/assets/node_modules/.bin/lark-cli --version
RUN --mount=type=cache,id=prd-assets-npm,target=/root/.npm,sharing=locked \
    npm ci --prefix apps/prd-review/frontend --ignore-scripts --no-audit --no-fund \
    && npm run --prefix apps/prd-review/frontend build

FROM ghcr.io/astral-sh/uv:0.11.19 AS uv

FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PRD_PROJECT_ROOT=/app/apps/prd-review DATA_DIR=/app/data HOME=/home/prd \
    UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy \
    COREPACK_HOME=/opt/corepack COREPACK_ENABLE_NETWORK=0 PATH=/opt/venv/bin:$PATH
WORKDIR /app/apps/prd-review
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --create-home prd \
    && install -d -o prd -g prd /app/data /app/agent-data
COPY --from=uv /uv /usr/local/bin/uv
COPY --from=dsh-build /usr/local/bin/node /usr/local/bin/node
COPY --from=dsh-build /usr/local/lib/node_modules/corepack /usr/local/lib/node_modules/corepack
COPY --from=dsh-build --chown=prd:prd /opt/corepack /opt/corepack
RUN ln -s /usr/local/lib/node_modules/corepack/dist/corepack.js /usr/local/bin/corepack
COPY --from=dsh-build /app /app
RUN --mount=type=cache,id=prd-uv,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-dev --no-editable
USER prd
RUN cd /app && corepack pnpm --version
EXPOSE 8000 3080
CMD ["gunicorn", "--no-control-socket", "--bind", "0.0.0.0:8000", "--threads", "4", "--timeout", "300", "prd_review.web.app:create_app()"]
