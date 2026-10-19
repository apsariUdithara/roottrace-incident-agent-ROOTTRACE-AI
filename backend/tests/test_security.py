import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import jwt
import pytest

from app.core import security
from app.core.config import get_settings
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    DecryptionError,
    InvalidTokenError,
    TokenType,
    create_access_token,
    create_refresh_token,
    decode_token,
    decrypt_secret,
    encrypt_secret,
    hash_password,
    password_needs_rehash,
    verify_password,
)

USER_ID = uuid.UUID("6f1c2a52-3a8e-4c39-9d1b-0d3f6a7b8c90")


@pytest.fixture(autouse=True)
def fresh_keys() -> Iterator[None]:
    """Keys are cached per process; clear them so a test that changes settings is isolated."""
    security._jwt_secret.cache_clear()
    security._fernet.cache_clear()
    yield
    security._jwt_secret.cache_clear()
    security._fernet.cache_clear()


# --- Passwords ---


async def test_password_round_trip() -> None:
    password_hash = await hash_password("correct horse battery staple")

    assert password_hash.startswith("$argon2id$")
    assert await verify_password(password_hash, "correct horse battery staple")
    assert not await verify_password(password_hash, "wrong password")
    assert not password_needs_rehash(password_hash)


async def test_same_password_hashes_differently() -> None:
    assert await hash_password("same") != await hash_password("same")  # random salt


async def test_verify_rejects_a_malformed_hash() -> None:
    assert not await verify_password("not-an-argon2-hash", "anything")


def test_dummy_hash_costs_the_same_as_a_real_one() -> None:
    # If argon2 defaults change, regenerate DUMMY_PASSWORD_HASH, or unknown emails get faster.
    assert not password_needs_rehash(DUMMY_PASSWORD_HASH)


# --- JWT ---


def test_access_token_round_trip() -> None:
    claims = decode_token(create_access_token(USER_ID), TokenType.ACCESS)

    assert claims.user_id == USER_ID
    assert claims.token_type is TokenType.ACCESS
    assert claims.expires_at > datetime.now(UTC)


def test_refresh_token_is_not_an_access_token() -> None:
    refresh = create_refresh_token(USER_ID)

    assert decode_token(refresh, TokenType.REFRESH).user_id == USER_ID
    with pytest.raises(InvalidTokenError, match="expected an access token"):
        decode_token(refresh, TokenType.ACCESS)


def test_each_token_has_its_own_id() -> None:
    first = decode_token(create_refresh_token(USER_ID), TokenType.REFRESH)
    second = decode_token(create_refresh_token(USER_ID), TokenType.REFRESH)

    assert first.token_id != second.token_id


def test_expired_token_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "access_token_minutes", -1)

    with pytest.raises(InvalidTokenError, match="expired"):
        decode_token(create_access_token(USER_ID), TokenType.ACCESS)


def test_tampered_token_is_rejected() -> None:
    header, payload, signature = create_access_token(USER_ID).split(".")
    forged_payload = jwt.utils.base64url_encode(
        f'{{"sub":"{uuid.uuid4()}","type":"access","jti":"x","iat":0,"exp":9999999999}}'.encode()
    ).decode()

    with pytest.raises(InvalidTokenError, match="Signature verification failed"):
        decode_token(f"{header}.{forged_payload}.{signature}", TokenType.ACCESS)


def test_token_signed_with_another_key_is_rejected() -> None:
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": str(USER_ID),
            "type": "access",
            "jti": "x",
            "iat": now,
            "exp": now.timestamp() + 60,
        },
        "an-attacker-key-that-is-long-enough-for-hs256",
        algorithm="HS256",
    )

    with pytest.raises(InvalidTokenError):
        decode_token(token, TokenType.ACCESS)


def test_unsigned_token_is_rejected() -> None:
    token = jwt.encode({"sub": str(USER_ID), "type": "access"}, None, algorithm="none")

    with pytest.raises(InvalidTokenError):
        decode_token(token, TokenType.ACCESS)


# --- Secrets at rest ---


def test_secret_round_trip() -> None:
    ciphertext = encrypt_secret("ghp_example_token")

    assert "ghp_example_token" not in ciphertext
    assert ciphertext != encrypt_secret("ghp_example_token")  # random IV each time
    assert decrypt_secret(ciphertext) == "ghp_example_token"


def test_tampered_secret_is_rejected() -> None:
    ciphertext = encrypt_secret("ghp_example_token")
    tampered = ciphertext[:-4] + ("AAAA" if not ciphertext.endswith("AAAA") else "BBBB")

    with pytest.raises(DecryptionError):
        decrypt_secret(tampered)
