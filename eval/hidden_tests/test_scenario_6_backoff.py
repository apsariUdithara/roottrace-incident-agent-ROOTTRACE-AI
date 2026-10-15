from paystream.payments.gateway import GatewayUnavailable, call_with_retry


def test_retries_wait_longer_each_time():
    waits = []
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 4:
            raise GatewayUnavailable()
        return "ok"

    assert call_with_retry(flaky, sleep=waits.append) == "ok"
    assert waits[0] > 0
    assert all(later > earlier for earlier, later in zip(waits, waits[1:]))
