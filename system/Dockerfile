FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY app ./app

RUN uv sync --frozen --extra postgres --extra graph --extra live --no-dev

EXPOSE 8787

CMD ["uv", "run", "--frozen", "python", "-m", "app"]
