import logging

import duckdb
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import BACKEND_DIR, ROOT_DIR, settings
from .logging_setup import RequestIdMiddleware, setup_logging
from .routers import chat, providers, semantic, sources
from .routers import settings as settings_router

setup_logging()
logger = logging.getLogger("app")


def run_migrations() -> None:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", settings.db_url)
    command.upgrade(cfg, "head")


def create_app() -> FastAPI:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    run_migrations()

    app = FastAPI(title="Semantic Layer AI", version="0.1.0")
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["x-request-id"],
    )
    for r in (providers.router, sources.router, semantic.router, chat.router, settings_router.router):
        app.include_router(r)

    @app.get("/health")
    def health():
        return {"status": "ok", "version": app.version, "duckdb": duckdb.__version__}

    # Serve the built frontend when present (single-process mode: `npm run build` first)
    dist = ROOT_DIR / "frontend" / "dist"
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            target = (dist / path).resolve()
            if path and target.is_file() and dist.resolve() in target.parents:
                return FileResponse(target)
            return FileResponse(dist / "index.html")

    return app


app = create_app()
