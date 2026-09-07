# Multi-stage: build the web app with pnpm, install Python with uv, ship a slim
# runtime that carries neither toolchain.

FROM node:22-slim AS web
WORKDIR /web
RUN corepack enable pnpm
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
# types.ts is generated and committed, so the web build needs no Python
RUN pnpm build


FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS python
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# dependencies first, so a source change does not re-resolve the world
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY sieve/ ./sieve/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


FROM python:3.12-slim-bookworm AS runtime
WORKDIR /app

RUN groupadd --system --gid 1000 sieve \
 && useradd --system --uid 1000 --gid sieve --create-home sieve

COPY --from=python --chown=sieve:sieve /app/.venv /app/.venv
COPY --from=python --chown=sieve:sieve /app/sieve /app/sieve
COPY --from=web --chown=sieve:sieve /web/build /app/web/build
COPY --chown=sieve:sieve data/axes /app/data/axes
COPY --chown=sieve:sieve profiles /app/profiles
COPY --chown=sieve:sieve sieve.toml.example /app/sieve.toml

# the store and the file target are the only things written at runtime
RUN mkdir -p /app/data /app/out && chown -R sieve:sieve /app/data /app/out

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SIEVE_CONFIG=/app/sieve.toml

USER sieve
EXPOSE 8110

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8110/healthz').status==200 else 1)"

# Secrets come from the environment at run time and are never baked in.
CMD ["uvicorn", "sieve.api.app:app", "--host", "0.0.0.0", "--port", "8110"]
