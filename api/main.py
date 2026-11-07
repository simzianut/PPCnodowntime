"""FastAPI app (spec 04 §6). API_FIXTURES=1 serves every route from fixtures/api/ (T19)."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.models import Health

CORS_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]
NOT_IMPLEMENTED = "real mode not implemented yet (T12); run with API_FIXTURES=1"


def fixtures_enabled() -> bool:
    return os.environ.get("API_FIXTURES", "0") == "1"


async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": jsonable_encoder(exc.errors())})


def create_app(fixtures: bool | None = None, manager=None) -> FastAPI:
    """Build the app; `manager` (a FixtureManager) lets tests inject a short recorded day."""
    use_fixtures = fixtures_enabled() if fixtures is None else fixtures
    if use_fixtures and manager is None:
        from api.fixture_mode import FixtureManager

        manager = FixtureManager()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        if manager is not None:
            await manager.shutdown()

    app = FastAPI(title="PPCnodowntime API", version="0.1", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.state.fixtures = use_fixtures
    app.state.manager = manager

    if use_fixtures:
        from api.fixture_mode import build_router

        app.include_router(build_router(manager))
        return app

    @app.get("/api/health", response_model=Health)
    async def health():
        return {"ok": True}

    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
    async def not_implemented(path: str):
        return JSONResponse(status_code=503, content={"detail": NOT_IMPLEMENTED})

    @app.websocket("/ws/{path:path}")
    async def ws_not_implemented(websocket: WebSocket, path: str):
        await websocket.accept()
        await websocket.close(code=1013, reason=NOT_IMPLEMENTED)

    return app


app = create_app()
