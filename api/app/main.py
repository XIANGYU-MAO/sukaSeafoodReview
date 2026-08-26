from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import admin, auth, exports, health, history, imports, progress, reviews, sync
from app.config import Settings, get_settings
from app.database import create_database_engine, create_session_factory
from app.services.auth import LoginLimiter, parse_trusted_proxy_networks


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime = settings or get_settings()
    if runtime.APP_ENV.lower() == "production" and not runtime.TRUSTED_PROXY_CIDRS:
        raise ValueError("TRUSTED_PROXY_CIDRS is required for the production API")
    Settings.validate_api_secrets(runtime)
    engine = create_database_engine(runtime)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await engine.dispose()

    app = FastAPI(title=runtime.app_name, lifespan=lifespan)
    app.state.settings = runtime
    app.state.session_factory = create_session_factory(engine)
    app.state.login_limiter = LoginLimiter()
    app.state.trusted_proxy_networks = parse_trusted_proxy_networks(
        getattr(runtime, "TRUSTED_PROXY_CIDRS", ())
    )
    app.include_router(health.router, prefix="/v1")
    app.include_router(auth.router, prefix="/v1")
    app.include_router(reviews.router, prefix="/v1")
    app.include_router(progress.router, prefix="/v1")
    app.include_router(history.router, prefix="/v1")
    app.include_router(admin.router, prefix="/v1")
    app.include_router(imports.router, prefix="/v1")
    app.include_router(exports.router, prefix="/v1")
    app.include_router(sync.router, prefix="/v1")
    return app
