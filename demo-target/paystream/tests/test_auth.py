from datetime import UTC, datetime, timedelta

import pytest

from paystream.auth.tokens import InvalidToken, issue_token, is_expired, verify


def test_new_token_is_not_expired():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    assert not is_expired(issue_token(7, now), now + timedelta(minutes=30))


def test_token_expires_after_ttl():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    assert is_expired(issue_token(7, now), now + timedelta(hours=2))


def test_missing_token_is_rejected():
    with pytest.raises(InvalidToken):
        verify(None)
