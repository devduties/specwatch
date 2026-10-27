import json

from fastapi.testclient import TestClient

from specwatch.config import Settings
from specwatch.fetcher import FetchResult
from specwatch.main import create_app
from specwatch.store import Store

BEFORE = {
    "openapi": "3.1.0",
    "info": {"title": "Orders API", "version": "1.0"},
    "paths": {
        "/orders": {
            "get": {
                "responses": {
                    "200": {
                        "description": "ok",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "id": {"type": "string"},
                                        "status": {"type": "string"},
                                    },
                                }
                            }
                        },
                    }
                }
            }
        }
    },
}
AFTER = {
    "openapi": "3.1.0",
    "info": {"title": "Orders API", "version": "2.0"},
    "paths": {
        "/orders": {
            "get": {
                "responses": {
                    "200": {
                        "description": "ok",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"id": {"type": "string"}},
                                }
                            }
                        },
                    }
                }
            }
        }
    },
}

class RecordingAlertSender:
    def __init__(self, settings: Settings) -> None:
        self.payloads: list[dict] = []

    async def __aenter__(self) -> "RecordingAlertSender":
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    async def send(self, payload: dict) -> bool:
        self.payloads.append(payload)
        return True

class FailingAlertSender(RecordingAlertSender):
    async def send(self, payload: dict) -> bool:
        raise RuntimeError("HookRelay is unavailable")

class FakeFetcher:
    def __init__(self, settings: Settings) -> None:
        self.documents = [BEFORE, AFTER]
        self.calls = 0

    async def __aenter__(self) -> "FakeFetcher":
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    async def fetch(
        self, url: str, *, etag: str | None = None, last_modified: str | None = None
    ) -> FetchResult:
        document = self.documents[min(self.calls, len(self.documents) - 1)]
        self.calls += 1
        return FetchResult(json.dumps(document).encode(), "application/json", None, None)

def test_monitor_registration_check_history_and_deletion(tmp_path) -> None:
    settings = Settings(tmp_path / "test.sqlite3", 0, 1024 * 1024, frozenset())
    app = create_app(settings, store=Store(settings.database_path), fetcher_factory=FakeFetcher)

    with TestClient(app) as client:
        assert client.get("/api/health").json()["status"] == "ok"
        created = client.post(
            "/api/monitors",
            json={"name": " Orders API ", "spec_url": "https://api.example.com/openapi.json"},
        )
        assert created.status_code == 201
        monitor_id = created.json()["id"]

        first = client.post(f"/api/monitors/{monitor_id}/check")
        assert first.status_code == 200
        assert first.json()["findings"] == []

        second = client.post(f"/api/monitors/{monitor_id}/check")
        assert second.status_code == 200
        assert any(item["code"] == "schema.property_removed" for item in second.json()["findings"])

        monitor = client.get(f"/api/monitors/{monitor_id}").json()
        assert monitor["status"] == "breaking"
        assert len(client.get(f"/api/monitors/{monitor_id}/snapshots").json()) == 2
        assert client.get("/api/overview").json()["breaking_changes"] == 1

        assert client.delete(f"/api/monitors/{monitor_id}").status_code == 204
        assert client.get("/api/monitors").json() == []

def test_http_spec_url_is_rejected_before_registration(tmp_path) -> None:
    settings = Settings(tmp_path / "test.sqlite3", 0, 1024 * 1024, frozenset())
    app = create_app(settings, store=Store(settings.database_path), fetcher_factory=FakeFetcher)
    with TestClient(app) as client:
        response = client.post(
            "/api/monitors",
            json={"name": "Insecure API", "spec_url": "http://api.example.com/openapi.json"},
        )
    assert response.status_code == 422

def test_breaking_change_sends_one_hookrelay_event(tmp_path) -> None:
    settings = Settings(
        tmp_path / "alerts.sqlite3",
        0,
        1024 * 1024,
        frozenset(),
        alert_url="http://127.0.0.1:8000/v1/endpoints/endpoint-1/events",
    )
    alert_senders: list[RecordingAlertSender] = []

    def alert_sender_factory(settings: Settings) -> RecordingAlertSender:
        sender = RecordingAlertSender(settings)
        alert_senders.append(sender)
        return sender

    app = create_app(
        settings,
        store=Store(settings.database_path),
        fetcher_factory=FakeFetcher,
        alert_sender_factory=alert_sender_factory,
    )

    with TestClient(app) as client:
        created = client.post(
            "/api/monitors",
            json={"name": "Orders API", "spec_url": "https://api.example.com/openapi.json"},
        )
        monitor_id = created.json()["id"]

        baseline = client.post(f"/api/monitors/{monitor_id}/check")
        breaking = client.post(f"/api/monitors/{monitor_id}/check")
        unchanged = client.post(f"/api/monitors/{monitor_id}/check")

    assert baseline.status_code == breaking.status_code == unchanged.status_code == 200
    assert len(alert_senders) == 1
    assert len(alert_senders[0].payloads) == 1
    alert = alert_senders[0].payloads[0]
    assert alert["event"] == "specwatch.breaking_change"
    assert alert["monitor_id"] == monitor_id
    assert alert["monitor_name"] == "Orders API"
    assert alert["spec_url"] == "https://api.example.com/openapi.json"
    assert [finding["code"] for finding in alert["findings"]] == ["schema.property_removed"]

def test_alert_failure_does_not_hide_saved_breaking_snapshot(tmp_path) -> None:
    settings = Settings(
        tmp_path / "alert-failure.sqlite3",
        0,
        1024 * 1024,
        frozenset(),
        alert_url="http://127.0.0.1:8000/v1/endpoints/endpoint-1/events",
    )
    app = create_app(
        settings,
        store=Store(settings.database_path),
        fetcher_factory=FakeFetcher,
        alert_sender_factory=FailingAlertSender,
    )

    with TestClient(app) as client:
        created = client.post(
            "/api/monitors",
            json={"name": "Orders API", "spec_url": "https://api.example.com/openapi.json"},
        )
        monitor_id = created.json()["id"]
        client.post(f"/api/monitors/{monitor_id}/check")
        response = client.post(f"/api/monitors/{monitor_id}/check")
        monitor = client.get(f"/api/monitors/{monitor_id}")

    assert response.status_code == 200
    assert monitor.status_code == 200
    assert monitor.json()["status"] == "breaking"
    assert any(item["code"] == "schema.property_removed" for item in monitor.json()["findings"])
