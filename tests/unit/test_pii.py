import pytest
from cryptography.fernet import Fernet

from app.config import get_settings
from app.services import pii


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("PII_ENCRYPTION_KEY", key)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_tokenize_replaces_names_and_keeps_map_encrypted():
    parties = [{"name": "Jane Roe", "account_id": "A1", "country": "US"}]
    redacted, mapping = pii.tokenize_parties(parties)
    assert "name" not in redacted[0]
    assert mapping == {"PARTY_001": "Jane Roe"}
    enc = pii.encrypt_map(mapping)
    assert "Jane" not in enc
    assert pii.decrypt_map(enc) == mapping


def test_scrub_removes_known_names():
    out = pii.scrub_text("Jane Roe moved funds.", ["Jane Roe"])
    assert "Jane Roe" not in out
