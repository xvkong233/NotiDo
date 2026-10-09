"""Bounded retries for reads. Writes never use this helper."""

import asyncio
import time


class ReadRetryBudget:
    def __init__(self, seconds):
        self.deadline = time.monotonic() + seconds
        self.failures = 0

    def remaining(self):
        value = self.deadline - time.monotonic()
        if value <= 0:
            raise TimeoutError
        return value

    async def backoff(self):
        if self.failures >= 2:
            return False
        delay = 0.25 * 2**self.failures
        self.failures += 1
        if self.remaining() <= delay:
            raise TimeoutError
        await asyncio.sleep(delay)
        self.remaining()
        return True
