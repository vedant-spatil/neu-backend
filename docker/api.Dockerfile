# API image for EC2 (plan.md section 23). Build context: backend/.
# Dependencies come from uv.lock, so the build is reproducible. The ml extra is not installed.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.25 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy \
    PATH=/app/.venv/bin:$PATH

WORKDIR /app

# Dependencies first, so code changes don't reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --frozen --no-dev

# spaCy model used by Presidio for PII redaction. uv venvs have no pip, so install it with uv.
RUN uv pip install --python /app/.venv/bin/python \
    "en_core_web_lg @ https://github.com/explosion/spacy-models/releases/download/en_core_web_lg-3.8.0/en_core_web_lg-3.8.0-py3-none-any.whl"

RUN useradd --create-home appuser \
    && mkdir -p /app/data/uploads && chown -R appuser /app/data
USER appuser

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

# One worker: the case graph and the DB connection pool are sized for one process on a t3a.large.
# --proxy-headers trusts X-Forwarded-* from nginx on the same Docker network only (see docker-compose.prod.yml).
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips=172.16.0.0/12"]
