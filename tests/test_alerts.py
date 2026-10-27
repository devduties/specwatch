import json
from pathlib import Path

import httpx
import pytest

from specwatch.alerts import AlertSender
from specwatch.config import Settings


def make_settings(*, alert_url: str | None, alert_api_key: str | None = None) -> Settings:
    return Settings(
        Path(":memory:"),
        0,
        1024 * 1024,
        frozenset(),
        alert_url=alert_url,
        alert_api_key=alert_api_key,
    )


@pytest.mark.asyncio
async def test_sender_posts_hookrelay_envelope_with_optional_bearer_key() -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(202, json={"status": "pending"})

    payload = {"event": "specwatch.breaking_change", "monitor_id": "monitor-1"}
    sender = AlertSender(
        make_settings(alert_url="http://hookrelay.local/events", alert_api_key="secret"),
        transport=httpx.MockTransport(handle),
    )

    async with sender:
        assert await sender.send(payload) is True

    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert str(requests[0].url) == "http://hookrelay.local/events"
    assert requests[0].headers["Authorization"] == "Bearer secret"
    assert json.loads(requests[0].content) == {"payload": payload}


@pytest.mark.asyncio
async def test_sender_is_disabled_without_alert_url() -> None:
    async with AlertSender(make_settings(alert_url=None)) as sender:
        assert await sender.send({"event": "specwatch.breaking_change"}) is None


@pytest.mark.asyncio
async def test_sender_logs_and_returns_false_for_hookrelay_error(caplog) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    sender = AlertSender(
        make_settings(alert_url="http://hookrelay.local/events"),
        transport=httpx.MockTransport(handle),
    )

    async with sender:
        sent = await sender.send(
            {"event": "specwatch.breaking_change", "monitor_id": "monitor-1"}
        )

    assert sent is False
    assert "Could not send breaking-change alert for monitor monitor-1" in caplog.text
