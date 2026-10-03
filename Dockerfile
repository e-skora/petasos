# Runs as root inside the machine: Fly does not document volume mount ownership,
# community reports say the mount is root-owned, and dropping privileges would need
# an extra tool in this image. Documented as the first hardening step after v1
# (docs/deploy.md).
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.21 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-install-project --no-dev

COPY src ./src
RUN uv sync --locked --no-dev

EXPOSE 8080

CMD ["python", "-m", "petasos.serve"]
