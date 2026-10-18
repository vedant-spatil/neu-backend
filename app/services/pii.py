"""PII handling before any LLM call (plan section 9).

- Party names and DOBs are replaced by stable tokens (PARTY_001, ...). The token map is kept
  Fernet-encrypted in cases.state and never sent to the LLM.
- Presidio scrubs anything left over in free text (emails, phones, other names).
"""

import json
import re
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet

from app.config import get_settings


def _fernet() -> Fernet:
    key = get_settings().pii_encryption_key
    if not key:
        raise RuntimeError("PII_ENCRYPTION_KEY not set (see backend/README.md)")
    return Fernet(key.encode())


def encrypt_map(mapping: dict[str, str]) -> str:
    return _fernet().encrypt(json.dumps(mapping).encode()).decode()


def decrypt_map(token: str) -> dict[str, str]:
    return json.loads(_fernet().decrypt(token.encode()).decode())


def tokenize_parties(parties: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Return (redacted party list, token->original map). Names/DOBs removed."""
    mapping: dict[str, str] = {}
    redacted: list[dict[str, Any]] = []
    for i, p in enumerate(parties, start=1):
        token = f"PARTY_{i:03d}"
        mapping[token] = p["name"]
        redacted.append({"party_token": token, "country": p.get("country"), "account_id": p["account_id"]})
    return redacted, mapping


def scrub_text(text: str, names: list[str]) -> str:
    """Replace known names, then run Presidio for residual PII."""
    for name in sorted(names, key=len, reverse=True):
        if name:
            text = re.sub(re.escape(name), "[PARTY]", text, flags=re.IGNORECASE)
    return _presidio().anonymize_text(text)


@lru_cache(maxsize=1)
def _presidio():
    return _PresidioScrubber()


class _PresidioScrubber:
    def __init__(self) -> None:
        from presidio_analyzer import AnalyzerEngine
        from presidio_anonymizer import AnonymizerEngine

        self._analyzer = AnalyzerEngine()
        self._anonymizer = AnonymizerEngine()

    def anonymize_text(self, text: str) -> str:
        results = self._analyzer.analyze(
            text=text,
            language="en",
            entities=["EMAIL_ADDRESS", "PHONE_NUMBER", "US_SSN", "CREDIT_CARD", "PERSON"],
        )
        return self._anonymizer.anonymize(text=text, analyzer_results=results).text
