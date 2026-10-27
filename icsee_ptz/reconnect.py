"""Bounded connection retry policy, shared by background tasks."""
from .config import safe_error


def retryable(exc):
    return safe_error(exc).get("kind") in ("connection", "timeout")


class Backoff:
    def __init__(self):
        self.attempts = 0

    def reset(self):
        self.attempts = 0

    def next(self):
        delay = min(30, 2 ** min(self.attempts + 1, 5))
        self.attempts += 1
        return delay
