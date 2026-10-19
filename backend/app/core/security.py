"""Password hashing (argon2id), JWT access/refresh tokens, and Fernet encryption of secrets."""

import asyncio
import base64
import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from functools import lru_cache

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Development and tests only: production refuses to start without JWT_SECRET and FERNET_KEY.
_DEV_JWT_SECRET = "roottrace-development-only-jwt-secret-never-use-in-production"
_DEV_FERNET_KEY = base64.urlsafe_b64encode(hashlib.sha256(b"roottrace-development-only").digest())


# --- Passwords ------------------------------------------------------------------------------

_hasher = PasswordHasher()  # argon2id with the RFC 9106 low-memory parameters

# Hash of a random, discarded password, with the same parameters as _hasher. Login verifies
# against it when the email is unknown, so "no such user" takes as long as "wrong password".
DUMMY_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$QP9oINSLE9Ygsx0Be0iQsg"
    "$OhNZkScfdjLvt663vMkM9gqVz8cze9vbyS1kLkuBoR8"
)


async def hash_password(password: str) -> str:
    # argon2 is slow on purpose (tens of ms), so keep it off the event loop.
    return await asyncio.to_thread(_hasher.hash, password)


async def verify_password(password_hash: str, password: str) -> bool:
    try:
        return await asyncio.to_thread(_hasher.verify, password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    """True when the hash uses older parameters; re-hash after the next successful login."""
    return _hasher.check_needs_rehash(password_hash)


# --- JWT --------------------------------------------------------------------------------------

JWT_ALGORITHM = "HS256"


class TokenType(StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


class InvalidTokenError(Exception):
    """The token is malformed, expired, tampered with, or of the wrong type."""


@dataclass(frozen=True)
class TokenClaims:
    user_id: uuid.UUID
    token_type: TokenType
    token_id: str  # jti, so a refresh token can be revoked later if needed
    expires_at: datetime


def create_access_token(user_id: uuid.UUID) -> str:
    lifetime = timedelta(minutes=get_settings().access_token_minutes)
    return _encode(user_id, TokenType.ACCESS, lifetime)


def create_refresh_token(user_id: uuid.UUID) -> str:
    lifetime = timedelta(days=get_settings().refresh_token_days)
    return _encode(user_id, TokenType.REFRESH, lifetime)


def decode_token(token: str, expected_type: TokenType) -> TokenClaims:
    """Verify signature, expiry and type. Raises InvalidTokenError on any problem."""
    try:
        payload = jwt.decode(
            token,
            _jwt_secret(),
            algorithms=[JWT_ALGORITHM],
            options={"require": ["sub", "type", "jti", "iat", "exp"]},
        )
        claims = TokenClaims(
            user_id=uuid.UUID(payload["sub"]),
            token_type=TokenType(payload["type"]),
            token_id=payload["jti"],
            expires_at=datetime.fromtimestamp(payload["exp"], UTC),
        )
    except (jwt.InvalidTokenError, ValueError) as exc:
        raise InvalidTokenError(str(exc)) from exc
    if claims.token_type is not expected_type:
        raise InvalidTokenError(f"expected an {expected_type} token, got {claims.token_type}")
    return claims


def _encode(user_id: uuid.UUID, token_type: TokenType, lifetime: timedelta) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "type": token_type.value,
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + lifetime,
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=JWT_ALGORITHM)


@lru_cache
def _jwt_secret() -> str:
    secret = get_settings().jwt_secret
    if secret is None:
        logger.warning("JWT_SECRET is not set: using the development-only secret")
        return _DEV_JWT_SECRET
    return secret.get_secret_value()


# --- Secrets at rest (Fernet) -------------------------------------------------------------------


class DecryptionError(Exception):
    """The value was tampered with, or encrypted with a different FERNET_KEY."""


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a secret, e.g. a GitHub token, for storage. Returns ASCII text."""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise DecryptionError("could not decrypt the stored secret") from exc


@lru_cache
def _fernet() -> Fernet:
    key = get_settings().fernet_key
    if key is None:
        logger.warning("FERNET_KEY is not set: using the development-only key")
        return Fernet(_DEV_FERNET_KEY)
    return Fernet(key.get_secret_value())
