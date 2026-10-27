"""Application orchestration for registering and checking API specs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any

from specwatch.alerts import AlertSender
from specwatch.diffing import compare_specs
from specwatch.errors import InvalidSpecDocument, InvalidSpecURL, SpecFetchError
from specwatch.fetcher import SpecFetcher, validate_url_shape
from specwatch.specs import parse_spec
from specwatch.store import Store

logger = logging.getLogger(__name__)


class SpecService:
    def __init__(
        self, store: Store, fetcher: SpecFetcher, alert_sender: AlertSender | None = None
    ) -> None:
        self.store = store
        self.fetcher = fetcher
        self.alert_sender = alert_sender
        self._locks: dict[str, asyncio.Lock] = {}

    def create_monitor(self, name: str, spec_url: str) -> dict[str, Any]:
        validate_url_shape(spec_url)
        return self.store.create_monitor(name.strip(), spec_url.strip())

    async def check_monitor(self, monitor_id: str) -> dict[str, Any]:
        lock = self._locks.setdefault(monitor_id, asyncio.Lock())
        async with lock:
            monitor = self.store.get_monitor_record(monitor_id)
            if not monitor:
                raise KeyError(monitor_id)
            previous = self.store.latest_snapshot(monitor_id)
            try:
                result = await self.fetcher.fetch(
                    monitor["spec_url"],
                    etag=monitor["etag"],
                    last_modified=monitor["last_modified"],
                )
                if result.not_modified:
                    checked_at = self.store.record_unchanged(
                        monitor_id, etag=result.etag, last_modified=result.last_modified
                    )
                    return {
                        "monitor_id": monitor_id,
                        "unchanged": True,
                        "checked_at": checked_at,
                        "findings": previous["findings"] if previous else [],
                    }

                parsed = parse_spec(result.content or b"", result.content_type)
                fingerprint = hashlib.sha256(
                    json.dumps(parsed.document, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
                findings = compare_specs(previous["document"], parsed.document) if previous else []
                snapshot = self.store.record_snapshot(
                    monitor_id,
                    title=parsed.title,
                    version=parsed.version,
                    fingerprint=fingerprint,
                    document=parsed.document,
                    findings=[item.to_dict() for item in findings],
                    etag=result.etag,
                    last_modified=result.last_modified,
                )
                breaking_findings = [item for item in findings if item.severity == "breaking"]
                if breaking_findings and self.alert_sender:
                    alert = {
                        "event": "specwatch.breaking_change",
                        "monitor_id": monitor["id"],
                        "monitor_name": monitor["name"],
                        "spec_url": monitor["spec_url"],
                        "snapshot_id": snapshot["id"],
                        "title": snapshot["title"],
                        "version": snapshot["version"],
                        "checked_at": snapshot["checked_at"],
                        "findings": [item.to_dict() for item in breaking_findings],
                    }
                    try:
                        await self.alert_sender.send(alert)
                    except Exception:
                        logger.exception(
                            "Could not send breaking-change alert for monitor %s.", monitor_id
                        )
                return {"monitor_id": monitor_id, "unchanged": False, **snapshot}
            except (InvalidSpecDocument, InvalidSpecURL, SpecFetchError) as exc:
                self.store.record_error(monitor_id, str(exc))
                raise


async def polling_loop(service: SpecService, interval_seconds: int) -> None:
    """Periodically refresh all monitors while isolating individual failures."""
    while True:
        await asyncio.sleep(interval_seconds)
        for monitor in service.store.list_monitors():
            try:
                await service.check_monitor(monitor["id"])
            except (InvalidSpecDocument, InvalidSpecURL, SpecFetchError):
                continue
