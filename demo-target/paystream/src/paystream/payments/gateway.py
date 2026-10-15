"""Client for the external card gateway, with retries and exponential backoff."""
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

MAX_RETRIES = 4
BACKOFF_BASE_SECONDS = 0.5
BACKOFF_CAP_SECONDS = 8.0


class GatewayUnavailable(Exception):
    pass


def backoff_delays(retries: int = MAX_RETRIES) -> list[float]:
    """Wait times between attempts: 0.5, 1, 2, 4 ... capped at 8 seconds."""
    return [min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * 2**i) for i in range(retries)]


def call_with_retry(fn: Callable[[], T], sleep: Callable[[float], None] = time.sleep) -> T:
    """Call fn, retrying on GatewayUnavailable with exponential backoff."""
    for delay in backoff_delays():
        try:
            return fn()
        except GatewayUnavailable:
            sleep(delay)
    return fn()
