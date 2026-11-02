# AgentCore Runtime image for one case run (plan: AgentCore for case runs, phase 2). Built for arm64 (AgentCore requirement).
# Same code and dependencies as the API image. The entrypoint is app/agentcore_app.py, which serves on port 8080.
#   docker buildx build --platform linux/arm64 -f docker/agentcore.Dockerfile -t <ecr-uri>:<tag> --push .
FROM --platform=linux/arm64 python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.25 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy \
    PATH=/app/.venv/bin:$PATH \
    CASE_RUNNER=local

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
RUN uv sync --frozen --no-dev

# The AgentCore SDK is installed separately so uv.lock stays the same for the API image.
RUN uv pip install --python /app/.venv/bin/python bedrock-agentcore
RUN uv pip install --python /app/.venv/bin/python \
    "en_core_web_lg @ https://github.com/explosion/spacy-models/releases/download/en_core_web_lg-3.8.0/en_core_web_lg-3.8.0-py3-none-any.whl"

EXPOSE 8080
CMD ["python", "-m", "app.agentcore_app"]
