"""Environment-backed application settings."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class Settings:
    database_path: Path
    poll_interval_seconds: int
    max_spec_bytes: int
    allowed_hosts: frozenset[str]
    api_key: str | None = None
    alert_url: str | None = None
    alert_api_key: str | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls) -> Settings:
        database_path = Path(os.getenv("SPECWATCH_DATABASE_PATH", "./data/specwatch.sqlite3"))
        poll_interval = int(os.getenv("SPECWATCH_POLL_INTERVAL_SECONDS", "300"))
        max_bytes = int(os.getenv("SPECWATCH_MAX_SPEC_BYTES", str(2 * 1024 * 1024)))
        hosts = frozenset(
            host.strip().lower().rstrip(".")
            for host in os.getenv("SPECWATCH_ALLOWED_HOSTS", "").split(",")
            if host.strip()
        )
        if poll_interval < 0:
            raise ValueError("SPECWATCH_POLL_INTERVAL_SECONDS must be zero or greater")
        if max_bytes < 1024:
            raise ValueError("SPECWATCH_MAX_SPEC_BYTES must be at least 1024")
        api_key = os.getenv("SPECWATCH_API_KEY") or None
        alert_url = os.getenv("SPECWATCH_ALERT_URL", "").strip() or None
        if alert_url:
            try:
                parsed_alert_url = urlsplit(alert_url)
                _ = parsed_alert_url.port
            except ValueError as exc:
                raise ValueError("SPECWATCH_ALERT_URL must be a valid HTTP(S) URL.") from exc
            if (
                parsed_alert_url.scheme.lower() not in {"http", "https"}
                or not parsed_alert_url.hostname
                or parsed_alert_url.username
                or parsed_alert_url.password
                or parsed_alert_url.fragment
            ):
                raise ValueError(
                    "SPECWATCH_ALERT_URL must be an HTTP(S) URL without credentials or a fragment."
                )
        alert_api_key = os.getenv("SPECWATCH_ALERT_API_KEY") or None
        return cls(
            database_path, poll_interval, max_bytes, hosts, api_key, alert_url, alert_api_key
        )
