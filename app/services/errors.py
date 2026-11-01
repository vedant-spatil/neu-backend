"""Labelled error messages for failed workflow steps (Neo4j, Postgres, Groq, Bedrock).

The audit timeline shows these, so an investigator sees which system failed and what to check. They never include query
parameters, payloads or secrets. Exceptions are matched by module and class name, so this module imports no drivers.
"""

from __future__ import annotations

MAX_TEXT = 300


def describe_error(exc: BaseException) -> str:
    module = type(exc).__module__ or ""
    name = type(exc).__name__
    text = " ".join(str(exc).split())

    if module.startswith("neo4j"):
        if name == "AuthError" or "Unauthorized" in text:
            return "Neo4j: login failed. Check NEO4J_USER and NEO4J_PASSWORD in .env, then restart the API."
        if name in ("ServiceUnavailable", "SessionExpired"):
            return "Neo4j: cannot reach the database. Check APP_NEO4J_URI and that the instance is running."
        return f"Neo4j: {name}: {text[:MAX_TEXT]}"

    if module.startswith("psycopg") or module.startswith("sqlalchemy"):
        if "password authentication failed" in text:
            return "Postgres: login failed. Check the password in APP_DATABASE_URL, then restart the API."
        if name == "OperationalError" or "connection failed" in text or "could not connect" in text:
            return "Postgres: cannot reach the database. Check APP_DATABASE_URL and that the Supabase project is running."
        return f"Postgres: {name}: {text[:MAX_TEXT]}"

    if module.startswith("botocore"):
        code = (getattr(exc, "response", None) or {}).get("Error", {}).get("Code", name)
        return f"Bedrock: {code}: {text[:MAX_TEXT]}"

    if name == "LlmError":
        provider, _, rest = text.partition(" ")
        label = {"groq": "Groq", "bedrock": "Bedrock"}.get(provider.lower(), "LLM")
        if "tool_use_failed" in text:
            return f"{label}: the model did not return the structured answer. Retry the case."
        if "HTTP 429" in text:
            return f"{label}: rate limit reached. Wait a minute, then retry the case."
        return f"{label}: {(rest if rest else text)[:MAX_TEXT]}"

    return f"{name}: {text[:MAX_TEXT]}"
