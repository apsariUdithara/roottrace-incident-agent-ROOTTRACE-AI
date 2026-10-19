"""Request and response bodies for /auth."""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field

# Emails are stored lowercased, so Alice@Example.com and alice@example.com are one account.
Email = Annotated[EmailStr, AfterValidator(str.lower)]

MAX_PASSWORD_LENGTH = 128  # argon2 accepts any length; the cap stops huge-input abuse


class RegisterRequest(BaseModel):
    email: Email
    password: str = Field(min_length=8, max_length=MAX_PASSWORD_LENGTH)


class LoginRequest(BaseModel):
    email: Email
    # No minimum here: a short password is just a wrong password (401), not a validation error.
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    created_at: datetime


class TokenResponse(BaseModel):
    """The access token goes in the body; the refresh token is set as an httpOnly cookie."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int  # seconds
    user: UserOut
