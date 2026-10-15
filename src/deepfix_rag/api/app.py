"""FastAPI application factory for the DeepFix product API."""

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from deepfix_rag.accounts import init_db
from deepfix_rag.api.routes import (
    admin,
    auth,
    documents,
    feedback,
    health,
    index,
    optimization,
    retrieval,
)
from deepfix_rag.config.settings import Settings
from deepfix_rag.config.settings import settings as default_settings
from deepfix_rag.documents import init_documents, prepare_workspace
from deepfix_rag.feedback import FeedbackWorker
from deepfix_rag.indexing import IndexCoordinator
from deepfix_rag.optimization import fail_interrupted, init_optimization, settings_from
from deepfix_rag.optimization_schedule import OptimizationScheduler


def validation_detail(exc: RequestValidationError) -> str:
    """Use the first field error as the response detail."""
    errors = exc.errors()
    if not errors:
        return "Invalid input."
    error = errors[0]
    ctx = error.get("ctx") or {}
    if "error" in ctx:
        return str(ctx["error"])
    return str(error.get("msg") or "Invalid input.")


def create_app(
    app_settings: Settings | None = None,
    *,
    embed_model: Any = None,
    llm: Any = None,
) -> FastAPI:
    """Build the API with its routers and CORS limited to the configured web origin."""
    app_settings = app_settings or default_settings

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(app.state.settings.product_db_path)
        init_documents(app.state.settings.product_db_path)
        init_optimization(app.state.settings.product_db_path)
        fail_interrupted(app.state.settings.product_db_path)
        prepare_workspace(app.state.settings.workspace_dir)
        coordinator = IndexCoordinator(
            app.state.settings.workspace_dir,
            app.state.settings.product_db_path,
            embed_model=embed_model,
            llm=llm,
        )
        app.state.index = coordinator
        coordinator.startup()
        worker = FeedbackWorker(app.state.settings.product_db_path)
        app.state.feedback = worker
        worker.start()
        scheduler = OptimizationScheduler(
            app.state.settings.product_db_path,
            app.state.settings.workspace_dir,
            options_for=lambda: settings_from(app.state.settings),
            coordinator_for=lambda: app.state.index,
        )
        app.state.optimization = scheduler
        scheduler.start()
        try:
            yield
        finally:
            scheduler.stop()
            worker.stop()
            coordinator.close()

    app = FastAPI(
        title="DeepFix Product API",
        version="0.1.0",
        description="HTTP boundary around the DeepFix domain layer.",
        lifespan=lifespan,
    )
    app.state.settings = app_settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[app_settings.WEB_ORIGIN],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": validation_detail(exc)})

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(admin.router)
    app.include_router(documents.router)
    app.include_router(index.router)
    app.include_router(retrieval.router)
    app.include_router(feedback.router)
    app.include_router(optimization.router)
    for route in app.routes:
        if isinstance(route, APIRoute):
            route.responses.pop(422, None)
    return app


app = create_app()


def _openapi_without_validation_status(schema: dict[str, Any]) -> dict[str, Any]:
    """Drop the unused 422 response FastAPI adds before routes can remove it."""
    for path in schema.get("paths", {}).values():
        for operation in path.values():
            if isinstance(operation, dict):
                operation.get("responses", {}).pop("422", None)
    return schema


_original_openapi = app.openapi


def _openapi() -> dict[str, Any]:
    schema = _original_openapi()
    return _openapi_without_validation_status(schema)


app.openapi = _openapi  # type: ignore[method-assign]
