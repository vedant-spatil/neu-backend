from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import auth, cases, health, imports, projects, users
from app.config import get_settings


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Create LangGraph checkpoint tables once, before any request can start a case.
    from app.orchestration.graph import get_graph

    get_graph()
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
    return app


app = create_app()
