FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1 UV_LINK_MODE=copy
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.12.24 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable && \
    groupadd --gid 10001 evaluator && \
    useradd --uid 10001 --gid evaluator --create-home evaluator && \
    mkdir runtime reports && chown evaluator:evaluator runtime reports
COPY datasets ./datasets
COPY scripts ./scripts
USER evaluator
EXPOSE 8000
CMD [".venv/bin/eval", "serve", "--host", "0.0.0.0", "--db", "runtime/demo.db"]
