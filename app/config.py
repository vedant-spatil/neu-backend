from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ folder, derived from this file so no machine-specific path is stored.
BACKEND_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    cors_origins: str = "http://localhost:5173"

    database_url: str = "postgresql+psycopg://aml:aml@localhost:5432/aml"

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "change-me-neo4j"

    yente_url: str = "http://localhost:8000"

    # LLM provider for app/services/llm.py: "groq" or "bedrock". Model names (below) are IDs for that provider.
    llm_provider: str = "groq"
    groq_api_key: str = ""
    bedrock_api_key: str = ""
    bedrock_region: str = "ap-south-1"
    llm_model_heavy: str = ""
    llm_model_light: str = ""

    pii_encryption_key: str = ""

    jwt_secret_key: str = "dev-only-change-me-to-32-random-bytes"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    triage_threshold: float = 0.6
    # XGBoost triage (plan.md section 24). "rules" keeps the rule score; "xgb" reads triage_scores for
    # TRIAGE_MODEL_RUN and opens a case when that score reaches TRIAGE_SCORE_THRESHOLD.
    triage_model: str = "rules"
    triage_model_run: str = ""
    triage_score_threshold: float = 0.6

    # Paths default to the current locations under BACKEND_ROOT. Override with env vars.
    # Planned move (plan.md section 22): data -> backend/data, checkpoints -> backend/artifacts/models.
    mgnn_dir: Path = BACKEND_ROOT / "external" / "Multi-GNN"
    mgnn_data_dir: Path = BACKEND_ROOT / "external" / "data"
    model_dir: Path = BACKEND_ROOT / "models"
    eval_dir: Path = BACKEND_ROOT / "docs" / "eval"
    raw_csv: Path = BACKEND_ROOT / "docs" / "HI-Small_Trans.csv"

    # Model run (app/pipeline). The old checkpoint: hidden 66, 2 layers.
    model_ckpt: Path = BACKEND_ROOT / "models" / "checkpoint_gin_small.tar"
    model_run_id: str = "model_h66"
    model_hidden: int = 66
    model_layers: int = 2
    model_neighbors: str = "10,10"
    model_chunk_size: int = 250_000
    model_batch_size: int = 4096
    alert_threshold: float = 0.40

    # Disabled: Celery and Redis are commented out (see app/tasks/__init__.py).
    # redis_url: str = "redis://localhost:6379/0"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def check_production(self) -> None:
        """With APP_ENV=prod, refuse to start on the development defaults (see plan section 23)."""
        if self.app_env != "prod":
            return
        if self.jwt_secret_key.startswith("dev-only") or len(self.jwt_secret_key) < 32:
            raise ValueError("APP_ENV=prod needs JWT_SECRET_KEY of 32+ characters, not the dev default")
        if self.neo4j_password == "change-me-neo4j":
            raise ValueError("APP_ENV=prod needs NEO4J_PASSWORD set, not the dev default")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.check_production()
    return settings
