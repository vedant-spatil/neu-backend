from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.auth.passwords import hash_password, verify_password
from app.auth.tokens import create_access_token, decode_access_token
from app.config import get_settings


def test_password_round_trip():
    h = hash_password("correct horse battery")
    assert verify_password("correct horse battery", h)
    assert not verify_password("wrong", h)


def test_token_round_trip():
    tok = create_access_token(7, "investigator")
    claims = decode_access_token(tok)
    assert claims["sub"] == "7"
    assert claims["role"] == "investigator"


def test_expired_token_rejected():
    s = get_settings()
    expired = jwt.encode(
        {"sub": "1", "role": "investigator", "exp": datetime.now(UTC) - timedelta(minutes=1)},
        s.jwt_secret_key,
        algorithm=s.jwt_algorithm,
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(expired)


def test_bad_signature_rejected():
    forged = jwt.encode({"sub": "1", "role": "investigator"}, "not-the-secret", algorithm="HS256")
    with pytest.raises(jwt.InvalidSignatureError):
        decode_access_token(forged)
