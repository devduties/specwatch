"""Best-effort delivery of breaking-change notifications."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from specwatch.config import Settings

logger = logging.getLogger(__name__)


class AlertSender:
    """Send SpecWatch events to a configured webhook endpoint."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport
        self.client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> AlertSender:
        if self.settings.alert_url:
            self.client = httpx.AsyncClient(
                timeout=httpx.Timeout(5.0, connect=2.0),
                follow_redirects=False,
                trust_env=False,
                transport=self.transport,
                headers={"User-Agent": "SpecWatch/0.1"},
            )
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self.client:
            await self.client.aclose()
            self.client = None

    async def send(self, payload: dict[str, Any]) -> bool | None:
        """Post a HookRelay-compatible event envelope; return None when disabled."""
        if not self.settings.alert_url:
            return None
        if not self.client:
            raise RuntimeError("AlertSender must be used as an async context manager.")

        headers = {}
        if self.settings.alert_api_key:
            headers["Authorization"] = f"Bearer {self.settings.alert_api_key}"
        try:
            response = await self.client.post(
                self.settings.alert_url,
                json={"payload": payload},
                headers=headers,
            )
            response.raise_for_status()
        except httpx.HTTPError:
            logger.warning(
                "Could not send breaking-change alert for monitor %s.",
                payload.get("monitor_id", "unknown"),
                exc_info=True,
            )
            return False
        return True
