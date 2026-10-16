# Authentication

PayStream issues session tokens that are valid for **one hour** (`TOKEN_TTL`).

## Time handling

All timestamps are **timezone-aware UTC** (`datetime.now(UTC)`).

Do not use `datetime.utcnow()`: it returns a *naive* datetime with no timezone attached. Python refuses to compare naive and aware datetimes and raises `TypeError: can't compare offset-naive and offset-aware datetimes`. In `verify`, any unexpected error while checking a token is treated as an invalid token, so this would surface as **HTTP 401 for every request**, i.e. users appear to be logged out.

## Verifying a token

`verify(token)`:

1. Rejects a missing token.
2. Rejects a token whose `expires_at` is in the past.
3. Treats any malformed token data as invalid.

All rejections raise `InvalidToken`, which the API returns as `401 Unauthorized`.

## Operational notes

A sudden spike in 401s that is not tied to a login outage usually means token verification is broken, not that users are misbehaving. Check recent changes to `auth/tokens.py` first.
