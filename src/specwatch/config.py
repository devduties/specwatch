"""Environment-backed application settings."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    database_path: Path
    poll_interval_seconds: int
    max_spec_bytes: int
    allowed_hosts: frozenset[str]

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
        return cls(database_path, poll_interval, max_bytes, hosts)
