from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import (
    auth,
    cases,
    health,
    imports,
    notifications,
    observability,
    projects,
    stream,
    users,
)
from app.config import get_settings


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Create LangGraph checkpoint tables once, before any request can start a case.
    from app.orchestration.graph import get_graph
    from app.services.sync import fail_interrupted_imports

    get_graph()
    # With TRIAGE_MODEL=xgb, warn now if the configured model run has no scores (cases would use rule scores).
    from app.services.triage_check import warn_if_no_scores

    warn_if_no_scores()
    # A sync thread does not survive a restart, so any import still busy at startup was interrupted.
    fail_interrupted_imports()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="AML Investigation Copilot", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(cases.router)
    app.include_router(imports.router)
    app.include_router(projects.router)
    app.include_router(users.router)
    app.include_router(notifications.router)
    app.include_router(stream.router)
    app.include_router(observability.router)
    return app


app = create_app()
