"""Failed steps show a labelled message: which system failed, and what to check."""

from app.services.errors import describe_error


def _exc(name: str, module: str, message: str = "") -> Exception:
    cls = type(name, (Exception,), {})
    cls.__module__ = module
    return cls(message)


def test_neo4j_login_failure_names_the_settings() -> None:
    msg = describe_error(_exc("AuthError", "neo4j.exceptions", "Unauthorized"))
    assert msg.startswith("Neo4j: login failed")
    assert "NEO4J_PASSWORD" in msg


def test_postgres_login_failure_names_the_url() -> None:
    msg = describe_error(_exc("OperationalError", "psycopg", 'password authentication failed for user "aml"'))
    assert msg.startswith("Postgres: login failed")
    assert "APP_DATABASE_URL" in msg


def test_groq_tool_failure_asks_for_a_retry() -> None:
    msg = describe_error(_exc("LlmError", "app.services.llm",
                              'groq openai/gpt-oss-20b rejected: HTTP 400: {"code":"tool_use_failed"}'))
    assert msg == "Groq: the model did not return the structured answer. Retry the case."


def test_bedrock_errors_are_labelled() -> None:
    msg = describe_error(_exc("LlmError", "app.services.llm", "bedrock anthropic.model rejected: HTTP 403: denied"))
    assert msg.startswith("Bedrock: anthropic.model rejected")
