"""Small SQLite repository for monitors, snapshots, and findings."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    """SQLite persistence with short-lived connections for thread safety."""

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = str(database_path)

    def _connect(self) -> sqlite3.Connection:
        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS monitors (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    spec_url TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_checked_at TEXT,
                    last_success_at TEXT,
                    last_error TEXT,
                    etag TEXT,
                    last_modified TEXT
                );
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    monitor_id TEXT NOT NULL REFERENCES monitors(id) ON DELETE CASCADE,
                    checked_at TEXT NOT NULL,
                    title TEXT NOT NULL,
                    version TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    document_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_snapshots_monitor_checked
                    ON snapshots(monitor_id, checked_at DESC);
                CREATE TABLE IF NOT EXISTS findings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
                    severity TEXT NOT NULL,
                    code TEXT NOT NULL,
                    path TEXT NOT NULL,
                    message TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_findings_snapshot
                    ON findings(snapshot_id, severity);
                """
            )

    def create_monitor(self, name: str, spec_url: str) -> dict[str, Any]:
        monitor_id = str(uuid.uuid4())
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO monitors (id, name, spec_url, created_at) VALUES (?, ?, ?, ?)",
                (monitor_id, name, spec_url, utc_now()),
            )
        return self.get_monitor(monitor_id)  # type: ignore[return-value]

    def list_monitors(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM monitors ORDER BY created_at DESC").fetchall()
        return [self._monitor_payload(dict(row)) for row in rows]

    def get_monitor(self, monitor_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM monitors WHERE id = ?", (monitor_id,)
            ).fetchone()
        return self._monitor_payload(dict(row)) if row else None

    def get_monitor_record(self, monitor_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM monitors WHERE id = ?", (monitor_id,)
            ).fetchone()
        return dict(row) if row else None

    def delete_monitor(self, monitor_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM monitors WHERE id = ?", (monitor_id,))
        return cursor.rowcount > 0

    def latest_snapshot(self, monitor_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM snapshots WHERE monitor_id = ? ORDER BY id DESC LIMIT 1",
                (monitor_id,),
            ).fetchone()
        if not row:
            return None
        payload = dict(row)
        payload["document"] = json.loads(payload.pop("document_json"))
        payload["findings"] = self.findings_for_snapshot(payload["id"])
        return payload

    def latest_snapshot_pair(self, monitor_id: str) -> list[dict[str, Any]]:
        """Return the latest two snapshots, oldest first, including their documents."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, monitor_id, checked_at, document_json FROM snapshots "
                "WHERE monitor_id = ? ORDER BY id DESC LIMIT 2",
                (monitor_id,),
            ).fetchall()
        snapshots = [dict(row) for row in reversed(rows)]
        for snapshot in snapshots:
            snapshot["document"] = json.loads(snapshot.pop("document_json"))
        return snapshots

    def list_snapshots(self, monitor_id: str, limit: int = 30) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, monitor_id, checked_at, title, version, fingerprint "
                "FROM snapshots WHERE monitor_id = ? ORDER BY id DESC LIMIT ?",
                (monitor_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def findings_for_snapshot(self, snapshot_id: int) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT severity, code, path, message FROM findings "
                "WHERE snapshot_id = ? ORDER BY CASE severity "
                "WHEN 'breaking' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, path",
                (snapshot_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_snapshot(
        self,
        monitor_id: str,
        *,
        title: str,
        version: str,
        fingerprint: str,
        document: dict[str, Any],
        findings: list[dict[str, str]],
        etag: str | None,
        last_modified: str | None,
    ) -> dict[str, Any]:
        checked_at = utc_now()
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO snapshots "
                "(monitor_id, checked_at, title, version, fingerprint, document_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    monitor_id,
                    checked_at,
                    title,
                    version,
                    fingerprint,
                    json.dumps(document, separators=(",", ":"), sort_keys=True),
                ),
            )
            snapshot_id = cursor.lastrowid
            connection.executemany(
                "INSERT INTO findings (snapshot_id, severity, code, path, message) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (snapshot_id, item["severity"], item["code"], item["path"], item["message"])
                    for item in findings
                ],
            )
            connection.execute(
                "UPDATE monitors SET last_checked_at = ?, last_success_at = ?, last_error = NULL, "
                "etag = ?, last_modified = ? WHERE id = ?",
                (checked_at, checked_at, etag, last_modified, monitor_id),
            )
        return {
            "id": snapshot_id,
            "monitor_id": monitor_id,
            "checked_at": checked_at,
            "title": title,
            "version": version,
            "fingerprint": fingerprint,
            "findings": findings,
        }

    def record_unchanged(
        self, monitor_id: str, *, etag: str | None, last_modified: str | None
    ) -> str:
        checked_at = utc_now()
        with self._connect() as connection:
            connection.execute(
                "UPDATE monitors SET last_checked_at = ?, last_success_at = ?, last_error = NULL, "
                "etag = COALESCE(?, etag), last_modified = COALESCE(?, last_modified) WHERE id = ?",
                (checked_at, checked_at, etag, last_modified, monitor_id),
            )
        return checked_at

    def record_error(self, monitor_id: str, message: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE monitors SET last_checked_at = ?, last_error = ? WHERE id = ?",
                (utc_now(), message[:500], monitor_id),
            )

    def overview(self) -> dict[str, Any]:
        with self._connect() as connection:
            monitor_count = connection.execute("SELECT COUNT(*) FROM monitors").fetchone()[0]
            snapshot_count = connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
            breaking_count = connection.execute(
                "SELECT COUNT(*) FROM findings WHERE severity = 'breaking'"
            ).fetchone()[0]
            error_count = connection.execute(
                "SELECT COUNT(*) FROM monitors WHERE last_error IS NOT NULL"
            ).fetchone()[0]
        return {
            "monitors": monitor_count,
            "snapshots": snapshot_count,
            "breaking_changes": breaking_count,
            "monitors_with_errors": error_count,
        }

    def _monitor_payload(self, record: dict[str, Any]) -> dict[str, Any]:
        latest = self.latest_snapshot(record["id"])
        record["latest_snapshot"] = (
            {key: latest[key] for key in ("id", "checked_at", "title", "version", "fingerprint")}
            if latest
            else None
        )
        record["findings"] = latest["findings"] if latest else []
        if record["last_error"]:
            record["status"] = "error"
        elif not latest:
            record["status"] = "pending"
        elif any(item["severity"] == "breaking" for item in record["findings"]):
            record["status"] = "breaking"
        elif record["findings"]:
            record["status"] = "changed"
        else:
            record["status"] = "healthy"
        return record
