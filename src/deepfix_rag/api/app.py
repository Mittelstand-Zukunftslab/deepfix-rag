"""FastAPI application factory for the DeepFix product API."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from deepfix_rag.api.routes import health
from deepfix_rag.config.settings import Settings
from deepfix_rag.config.settings import settings as default_settings


def create_app(app_settings: Settings | None = None) -> FastAPI:
    """Build the API with its routers and CORS limited to the configured web origin."""
    app_settings = app_settings or default_settings
    app = FastAPI(
        title="DeepFix Product API",
        version="0.1.0",
        description="HTTP boundary around the DeepFix domain layer.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[app_settings.WEB_ORIGIN],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    return app


app = create_app()
