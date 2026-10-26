from fastapi.testclient import TestClient

from specwatch.config import Settings
from specwatch.main import create_app
from specwatch.store import Store


def make_app(tmp_path, api_key: str | None):
    settings = Settings(tmp_path / "auth.sqlite3", 0, 1024 * 1024, frozenset(), api_key)
    return create_app(settings, store=Store(settings.database_path))


def test_missing_bearer_token_is_rejected(tmp_path) -> None:
    with TestClient(make_app(tmp_path, "test-secret")) as client:
        response = client.get("/api/health")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_wrong_bearer_token_is_rejected(tmp_path) -> None:
    with TestClient(make_app(tmp_path, "test-secret")) as client:
        response = client.get("/api/health", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401


def test_correct_bearer_token_is_accepted(tmp_path) -> None:
    with TestClient(make_app(tmp_path, "test-secret")) as client:
        response = client.get("/api/health", headers={"Authorization": "Bearer test-secret"})
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_authentication_is_optional_when_api_key_is_unset(tmp_path) -> None:
    with TestClient(make_app(tmp_path, None)) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
