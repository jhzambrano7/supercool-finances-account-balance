# The service's image. Two targets: `dev` (hot reload, used by docker-compose.yml) and `runtime`
# (non-root, no reload, no dev dependencies) -- the same base layers feed both, so what runs in
# development is not a different lineage from what would ship.
#
# `UV_PROJECT_ENVIRONMENT=/opt/venv` is the load-bearing choice here. The default `.venv` would sit
# at `/app/.venv`, exactly where docker-compose.yml bind-mounts the host repository -- and the host
# venv is built for macOS, so the mount would shadow the image's Linux one with binaries that
# cannot run. Keeping the environment outside the mounted tree removes the collision entirely,
# rather than papering over it with an anonymous volume that then goes stale on every lock change.

FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS base

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first, project second: `pyproject.toml`/`uv.lock` change far less often than `src`,
# so editing a use case does not re-resolve the dependency tree.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY alembic.ini ./
COPY alembic ./alembic
COPY src ./src

# Installs the project itself (editable): what puts `modules.*` on `sys.path`, as pyproject.toml
# explains. The editable finder resolves to `/app/src`, which the bind mount keeps valid -- that is
# what makes the reloader see host edits at all.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


FROM base AS dev

# Dev group: uvicorn's reloader needs nothing from it, but a shell in this container is where you
# run pytest, ruff and mypy, and having them absent would push that work back onto the host.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen

COPY docker/api-entrypoint.sh /usr/local/bin/api-entrypoint
RUN chmod +x /usr/local/bin/api-entrypoint

EXPOSE 8000
ENTRYPOINT ["api-entrypoint"]
CMD ["uvicorn", "modules.shared.adapters.inbound.api.app:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--reload", "--reload-dir", "/app/src"]


FROM base AS runtime

# Non-root: this target has no bind mount, so nothing needs to write into the tree at runtime.
RUN useradd --create-home --uid 10001 app && chown -R app:app /app
USER app

EXPOSE 8000
# No `--reload` and no migration step: a production process must not mutate the schema it happens
# to start against -- that is a deployment step with its own gate, not a side effect of booting.
CMD ["uvicorn", "modules.shared.adapters.inbound.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
