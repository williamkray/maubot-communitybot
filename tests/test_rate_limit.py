"""Tests for the rate-limit retry helper in common_utils."""

import pytest
from unittest.mock import AsyncMock, patch

from mautrix.errors import MLimitExceeded

from community.helpers.common_utils import (
    with_rate_limit_retry,
    _extract_retry_after_seconds,
)


class _Log:
    """Minimal logger stub that swallows warnings."""

    def warning(self, *a, **k):
        pass


def _limit(msg="Too Many Requests"):
    return MLimitExceeded(429, msg)


@pytest.mark.asyncio
async def test_returns_result_without_retry():
    call = AsyncMock(return_value="ok")
    result = await with_rate_limit_retry(lambda: call(), log=_Log())
    assert result == "ok"
    assert call.call_count == 1


@pytest.mark.asyncio
async def test_retries_then_succeeds():
    call = AsyncMock(side_effect=[_limit(), _limit(), "ok"])
    with patch(
        "community.helpers.common_utils.asyncio.sleep", new=AsyncMock()
    ) as sleep:
        result = await with_rate_limit_retry(
            lambda: call(), log=_Log(), base_delay=0.01
        )
    assert result == "ok"
    assert call.call_count == 3
    assert sleep.await_count == 2  # slept before each retry


@pytest.mark.asyncio
async def test_raises_after_exhausting_retries():
    call = AsyncMock(side_effect=_limit())
    with patch("community.helpers.common_utils.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(MLimitExceeded):
            await with_rate_limit_retry(
                lambda: call(), log=_Log(), max_retries=2, base_delay=0.01
            )
    assert call.call_count == 3  # initial + 2 retries


@pytest.mark.asyncio
async def test_aborts_when_wait_too_long():
    # server asks for 120s; abort_over is 60s -> give up immediately, no sleep
    call = AsyncMock(side_effect=_limit("Too Many Requests retry_after_ms:120000"))
    with patch(
        "community.helpers.common_utils.asyncio.sleep", new=AsyncMock()
    ) as sleep:
        with pytest.raises(MLimitExceeded):
            await with_rate_limit_retry(
                lambda: call(), log=_Log(), abort_over=60.0
            )
    assert call.call_count == 1
    assert sleep.await_count == 0


@pytest.mark.asyncio
async def test_honors_server_retry_after():
    call = AsyncMock(side_effect=[_limit("slow down retry_after_ms:2000"), "ok"])
    with patch(
        "community.helpers.common_utils.asyncio.sleep", new=AsyncMock()
    ) as sleep:
        result = await with_rate_limit_retry(lambda: call(), log=_Log())
    assert result == "ok"
    # waited the server-requested 2.0s, not the 1.0s default backoff
    sleep.assert_awaited_once_with(2.0)


def test_extract_retry_after_present():
    assert _extract_retry_after_seconds(_limit("x retry_after_ms:4500")) == 4.5


def test_extract_retry_after_absent():
    assert _extract_retry_after_seconds(_limit("Too Many Requests")) is None
