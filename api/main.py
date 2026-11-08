"""FastAPI app (spec 04 §6): API_FIXTURES=1 serves fixtures/api/ (T19), else engine replays."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.auth import AccessKey, AccessKeyMiddleware, access_key_from_env, build_auth_router

CORS_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


def fixtures_enabled() -> bool:
    return os.environ.get("API_FIXTURES", "0") == "1"


async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": jsonable_encoder(exc.errors())})


def create_app(
    fixtures: bool | None = None, manager=None, access: AccessKey | None = None
) -> FastAPI:
    """Build the app; `manager` (FixtureManager or EngineManager) lets tests inject small data."""
    use_fixtures = fixtures_enabled() if fixtures is None else fixtures
    if manager is None:
        if use_fixtures:
            from api.fixture_mode import FixtureManager

            manager = FixtureManager()
        else:
            from api.engine_mode import EngineManager

            manager = EngineManager()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if not use_fixtures:
            await manager.startup()
        yield
        await manager.shutdown()

    app = FastAPI(title="PPCnodowntime API", version="0.2", lifespan=lifespan)
    # Access key (decision 0087): off unless PPC_AUTH=on or PPC_ACCESS_KEY is set.
    # Added before CORS so CORS stays outermost and a 401 still carries CORS headers.
    access = access_key_from_env() if access is None else access
    app.state.access = access
    app.add_middleware(AccessKeyMiddleware, access=access)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,
    )
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.state.fixtures = use_fixtures
    app.state.manager = manager

    if use_fixtures:
        from api.fixture_mode import build_router
    else:
        from api.engine_mode import build_router
    app.include_router(build_router(manager))
    app.include_router(build_auth_router(access))
    return app


app = create_app()
