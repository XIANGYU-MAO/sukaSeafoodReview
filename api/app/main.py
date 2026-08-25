from fastapi import FastAPI

from app.api.routes import health
from app.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime = settings or get_settings()
    app = FastAPI(title=runtime.app_name)
    app.include_router(health.router, prefix="/v1")
    return app
