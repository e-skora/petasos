# Not built in CI for this change (spec 0.7 acceptance test 8). Change 005 decides
# where the tokens come from at deploy time; until then this entrypoint is not run.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project --no-dev

COPY src ./src
RUN uv sync --locked --no-dev

EXPOSE 8080

CMD ["uv", "run", "uvicorn", "petasos.app:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8080", "--timeout-graceful-shutdown", "30"]
