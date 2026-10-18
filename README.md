# AML Copilot backend (POC)

Plan: `../plan.md`. Dataset: `docs/HI-Small_Trans.csv` (IBM AML synthetic, Kaggle; check its licence on the Kaggle page; do not commit).
Sanctions data: OpenSanctions bulk, CC BY-NC 4.0, non-commercial only.

## Setup

```bash
cp .env.example .env            # fill GROQ_API_KEY, PII_ENCRYPTION_KEY, JWT_SECRET_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"   # PII_ENCRYPTION_KEY
python -c "import secrets; print(secrets.token_urlsafe(48))"                                  # JWT_SECRET_KEY
uv venv .venv --python 3.12 && uv pip install -e ".[dev]" --python .venv/bin/python
python -m spacy download en_core_web_lg --no-deps   # Presidio model, needs pip
```

WSL2: set `memory=12GB` in `%UserProfile%\.wslconfig`, then `wsl --shutdown`.

## Run

```bash
docker compose up -d postgres neo4j elasticsearch yente
.venv/bin/alembic upgrade head
.venv/bin/python -m app.ingest.load --csv docs/HI-Small_Trans.csv
.venv/bin/python -m app.cli.create_user --email you@example.com --password '...'
.venv/bin/uvicorn app.main:app --port 8000
```

## Tests

```bash
.venv/bin/python -m pytest -q tests/unit
```
