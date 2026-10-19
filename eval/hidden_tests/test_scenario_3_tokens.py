from paystream.auth.tokens import issue_token, verify


def test_fresh_token_is_accepted_with_real_clock():
    assert verify(issue_token(7)) == 7
