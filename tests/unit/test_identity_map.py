"""Investigator identity map: stored encrypted, decrypted only through the key, never plain."""

from types import SimpleNamespace

from cryptography.fernet import Fernet

from app.services import pii


def test_identity_map_round_trips_and_is_not_plain(monkeypatch) -> None:
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(pii, "get_settings", lambda: SimpleNamespace(pii_encryption_key=key))
    mapping = {"PARTY_001": "Jane Doe", "PARTY_002": "John Roe"}
    token = pii.encrypt_map(mapping)
    assert "Jane Doe" not in token
    assert pii.decrypt_map(token) == mapping
