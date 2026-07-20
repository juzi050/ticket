import pytest

from app.retry import RetryPolicy, retry_async


async def test_retry_until_success() -> None:
    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError("temporary")
        return "ok"

    result = await retry_async(
        operation,
        RetryPolicy(max_attempts=3, base_delay_seconds=0, jitter_seconds=0),
    )
    assert result == "ok"
    assert attempts == 3


async def test_retry_exhausted() -> None:
    async def operation() -> None:
        raise RuntimeError("always")

    with pytest.raises(RuntimeError, match="always"):
        await retry_async(
            operation,
            RetryPolicy(max_attempts=2, base_delay_seconds=0, jitter_seconds=0),
        )
