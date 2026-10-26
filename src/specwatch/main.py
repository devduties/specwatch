"""FastAPI application factory and HTTP interface."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from specwatch.auth import require_api_key
from specwatch.config import Settings
from specwatch.errors import InvalidSpecDocument, InvalidSpecURL, SpecFetchError
from specwatch.fetcher import SpecFetcher
from specwatch.service import SpecService, polling_loop
from specwatch.store import Store


class MonitorCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=2, max_length=100)
    spec_url: str = Field(min_length=8, max_length=2048)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Monitor name cannot be blank.")
        return value


def create_app(
    settings: Settings | None = None,
    *,
    store: Store | None = None,
    fetcher_factory: Any = SpecFetcher,
) -> FastAPI:
    settings = settings or Settings.from_env()
    store = store or Store(settings.database_path)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        store.initialize()
        async with fetcher_factory(settings) as fetcher:
            service = SpecService(store, fetcher)
            application.state.service = service
            poller = None
            if settings.poll_interval_seconds > 0:
                poller = asyncio.create_task(polling_loop(service, settings.poll_interval_seconds))
            try:
                yield
            finally:
                if poller:
                    poller.cancel()
                    try:
                        await poller
                    except asyncio.CancelledError:
                        pass

    app = FastAPI(
        title="SpecWatch",
        description="Monitor OpenAPI documents and review potentially breaking contract changes.",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/", include_in_schema=False)
    def dashboard() -> FileResponse:
        return FileResponse(Path(__file__).parent / "static" / "index.html")

    router = APIRouter(prefix="/api")

    @router.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "specwatch"}

    @router.get("/overview", tags=["monitors"])
    def overview(request: Request) -> dict[str, Any]:
        return request.app.state.service.store.overview()

    @router.get("/monitors", tags=["monitors"])
    def list_monitors(request: Request) -> list[dict[str, Any]]:
        return request.app.state.service.store.list_monitors()

    @router.post("/monitors", status_code=201, tags=["monitors"])
    def create_monitor(payload: MonitorCreate, request: Request) -> dict[str, Any]:
        try:
            return request.app.state.service.create_monitor(payload.name, payload.spec_url)
        except InvalidSpecURL as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/monitors/{monitor_id}", tags=["monitors"])
    def get_monitor(monitor_id: str, request: Request) -> dict[str, Any]:
        result = request.app.state.service.store.get_monitor(monitor_id)
        if not result:
            raise HTTPException(status_code=404, detail="Monitor not found.")
        return result

    @router.delete("/monitors/{monitor_id}", status_code=204, tags=["monitors"])
    def delete_monitor(monitor_id: str, request: Request) -> None:
        if not request.app.state.service.store.delete_monitor(monitor_id):
            raise HTTPException(status_code=404, detail="Monitor not found.")

    @router.post("/monitors/{monitor_id}/check", tags=["checks"])
    async def check_monitor(monitor_id: str, request: Request) -> dict[str, Any]:
        try:
            return await request.app.state.service.check_monitor(monitor_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Monitor not found.") from exc
        except (InvalidSpecURL, InvalidSpecDocument, SpecFetchError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.get("/monitors/{monitor_id}/snapshots", tags=["checks"])
    def list_snapshots(monitor_id: str, request: Request, limit: int = 30) -> list[dict[str, Any]]:
        if not request.app.state.service.store.get_monitor_record(monitor_id):
            raise HTTPException(status_code=404, detail="Monitor not found.")
        if not 1 <= limit <= 100:
            raise HTTPException(status_code=422, detail="Limit must be between 1 and 100.")
        return request.app.state.service.store.list_snapshots(monitor_id, limit)

    @router.get("/monitors/{monitor_id}/findings", tags=["checks"])
    def latest_findings(monitor_id: str, request: Request) -> list[dict[str, Any]]:
        if not request.app.state.service.store.get_monitor_record(monitor_id):
            raise HTTPException(status_code=404, detail="Monitor not found.")
        latest = request.app.state.service.store.latest_snapshot(monitor_id)
        return latest["findings"] if latest else []

    app.include_router(router, dependencies=[Depends(require_api_key(settings.api_key))])
    return app


app = create_app()
