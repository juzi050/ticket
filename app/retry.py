from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 1
    max_delay_seconds: float = 30
    jitter_seconds: float = 0.5
    retry_exceptions: tuple[type[BaseException], ...] = (Exception,)


async def retry_async(operation: Callable[[], Awaitable[T]], policy: RetryPolicy) -> T:
    last_error: BaseException | None = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return await operation()
        except policy.retry_exceptions as exc:
            last_error = exc
            if attempt >= policy.max_attempts:
                raise
            delay = min(policy.base_delay_seconds * (2 ** (attempt - 1)), policy.max_delay_seconds)
            delay += random.uniform(0, policy.jitter_seconds)
            await asyncio.sleep(delay)
    assert last_error is not None
    raise last_error
