"""FastAPI application factory for the DeepFix product API."""

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.formparsers import MultiPartParser

from deepfix_rag.api.identity import TokenVerifier
from deepfix_rag.api.routes import (
    documents,
    feedback,
    health,
    history,
    index,
    optimization,
    retrieval,
)
from deepfix_rag.config.settings import LOGGER, Settings
from deepfix_rag.config.settings import settings as default_settings
from deepfix_rag.documents import MAX_UPLOAD_BYTES, init_documents, prepare_workspace
from deepfix_rag.feedback import FeedbackWorker
from deepfix_rag.indexing import IndexCoordinator
from deepfix_rag.optimization import fail_interrupted, init_optimization, settings_from
from deepfix_rag.optimization_schedule import OptimizationScheduler
from deepfix_rag.storage import drop_account_tables

# Starlette defaults max_part_size to 1MB (1024 * 1024), which causes any multipart field
# (such as client-side extracted PDF readings or large documents up to MAX_UPLOAD_BYTES) to be
# rejected with MultiPartException: "Part exceeded maximum size of 1024KB".
# Increase max_part_size to at least MAX_UPLOAD_BYTES + 5MB margin.
_DEFAULT_MAX_PART_SIZE = MAX_UPLOAD_BYTES + 5 * 1024 * 1024
_original_multipart_init = MultiPartParser.__init__


def _patched_multipart_init(
    self: MultiPartParser,
    headers: Any,
    stream: Any,
    *,
    max_files: int | float = 1000,
    max_fields: int | float = 1000,
    max_part_size: int = 1024 * 1024,
) -> None:
    if max_part_size < _DEFAULT_MAX_PART_SIZE:
        max_part_size = _DEFAULT_MAX_PART_SIZE
    _original_multipart_init(
        self,
        headers,
        stream,
        max_files=max_files,
        max_fields=max_fields,
        max_part_size=max_part_size,
    )


MultiPartParser.__init__ = _patched_multipart_init


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
    token_verifier: TokenVerifier | None = None,
) -> FastAPI:
    """Build the API with its routers and CORS limited to the configured web origin.

    ``token_verifier`` checks the identity tokens the web app attaches. By default it fetches
    keys from ``settings.auth_jwks_url``.
    """
    app_settings = app_settings or default_settings

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        drop_account_tables(app.state.settings.product_db_path)
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
    app.state.token_verifier = token_verifier or TokenVerifier(
        issuer=app_settings.auth_issuer,
        audience=app_settings.auth_audience,
        jwks_url=app_settings.auth_jwks_url,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[app_settings.WEB_ORIGIN],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, exc: RequestValidationError) -> JSONResponse:
        LOGGER.warning("Request validation failed on %s: %s", _request.url.path, exc.errors())
        return JSONResponse(status_code=400, content={"detail": validation_detail(exc)})

    app.include_router(health.router)
    app.include_router(documents.router)
    app.include_router(index.router)
    app.include_router(retrieval.router)
    app.include_router(feedback.router)
    app.include_router(history.router)
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
