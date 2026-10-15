from paystream.payments.gateway import GatewayUnavailable, call_with_retry


def test_returns_result_on_first_success():
    assert call_with_retry(lambda: "ok", sleep=lambda _: None) == "ok"


def test_retries_until_success():
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise GatewayUnavailable()
        return "ok"

    assert call_with_retry(flaky, sleep=lambda _: None) == "ok"
    assert len(attempts) == 3
