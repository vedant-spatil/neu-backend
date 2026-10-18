"""APP_ENV=prod must refuse the development defaults (plan.md section 23)."""

import pytest

from app.config import Settings

STRONG_JWT = "x" * 40


def test_dev_settings_are_accepted_without_prod_checks():
    Settings(app_env="dev", jwt_secret_key="dev-only-change-me-to-32-random-bytes").check_production()


def test_prod_refuses_default_jwt_secret():
    s = Settings(app_env="prod", jwt_secret_key="dev-only-change-me-to-32-random-bytes", neo4j_password="real-pass")
    with pytest.raises(ValueError, match="JWT_SECRET_KEY"):
        s.check_production()


def test_prod_refuses_short_jwt_secret():
    s = Settings(app_env="prod", jwt_secret_key="short", neo4j_password="real-pass")
    with pytest.raises(ValueError, match="JWT_SECRET_KEY"):
        s.check_production()


def test_prod_refuses_default_neo4j_password():
    s = Settings(app_env="prod", jwt_secret_key=STRONG_JWT, neo4j_password="change-me-neo4j")
    with pytest.raises(ValueError, match="NEO4J_PASSWORD"):
        s.check_production()


def test_prod_accepts_real_secrets():
    Settings(app_env="prod", jwt_secret_key=STRONG_JWT, neo4j_password="real-pass").check_production()
