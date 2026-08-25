from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import auth, health
from app.config import Settings, get_settings
from app.database import create_database_engine, create_session_factory
from app.services.auth import LoginLimiter


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime = settings or get_settings()
    engine = create_database_engine(runtime)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await engine.dispose()

    app = FastAPI(title=runtime.app_name, lifespan=lifespan)
    app.state.settings = runtime
    app.state.session_factory = create_session_factory(engine)
    app.state.login_limiter = LoginLimiter()
    app.include_router(health.router, prefix="/v1")
    app.include_router(auth.router, prefix="/v1")
    return app
