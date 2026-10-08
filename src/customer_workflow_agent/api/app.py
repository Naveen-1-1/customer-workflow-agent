"""FastAPI app: `uvicorn --factory customer_workflow_agent.api.app:create_app`."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from prometheus_fastapi_instrumentator import Instrumentator

from customer_workflow_agent.api import health
from customer_workflow_agent.api.appdb import AppDB
from customer_workflow_agent.api.events import EventBus
from customer_workflow_agent.api.routes import router
from customer_workflow_agent.api.runner import ApiError, ChatRunner
from customer_workflow_agent.deps import Deps
from customer_workflow_agent.graph.builder import build_graph
from customer_workflow_agent.llm.service import LLMService, RouterLLMService, UnavailableLLM
from customer_workflow_agent.obs import metrics
from customer_workflow_agent.obs.logs import ChatIdMiddleware, configure_logging
from customer_workflow_agent.settings import PROJECT_ROOT, Settings, get_settings
from customer_workflow_agent.store import FileBackend, RetailStore

log = logging.getLogger(__name__)
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"


@dataclass
class Services:
    settings: Settings
    store: RetailStore
    runner: ChatRunner
    bus: EventBus
    llm_configured: bool


def _default_llm(settings: Settings) -> tuple[LLMService, bool]:
    if settings.nvidia_api_key is None:
        log.warning("NVIDIA_API_KEY is not set: the agent will apologize instead of answering")
        return UnavailableLLM("NVIDIA_API_KEY is not set"), False
    return RouterLLMService(settings), True


def create_app(
    settings: Settings | None = None,
    llm: LLMService | None = None,
    frontend_dist: Path | None = FRONTEND_DIST,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.var_dir.mkdir(parents=True, exist_ok=True)
        store = RetailStore(FileBackend(settings.db_original, settings.db_working))
        service, configured = (llm, True) if llm is not None else _default_llm(settings)
        appdb = await AppDB.open(settings.app_db)
        async with AsyncSqliteSaver.from_conn_string(str(settings.checkpoint_db)) as saver:
            await saver.setup()
            graph = build_graph(Deps(store=store, llm=service, settings=settings), saver)
            bus = EventBus()
            runner = ChatRunner(graph, saver, store, appdb, bus, settings)
            await runner.rebuild_approvals()
            app.state.services = Services(settings, store, runner, bus, configured)
            try:
                yield
            finally:
                await runner.shutdown()
                await appdb.close()

    app = FastAPI(title="Customer workflow agent", lifespan=lifespan)
    app.add_middleware(ChatIdMiddleware)

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content={"error": {"code": exc.code, "message": exc.message}},
            headers=exc.headers,
        )

    app.include_router(router, prefix="/api")
    app.include_router(health.router)
    # HTTP request metrics; live-update streams stay open for minutes, so they're left out.
    Instrumentator(excluded_handlers=["/metrics", "/healthz", "/readyz", ".*/events$"]).add(
        metrics.HTTP
    ).instrument(app)

    # Single-process demo: serve the built React app (npm run build) if it exists.
    if frontend_dist is not None and (frontend_dist / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="assets")

        root = frontend_dist.resolve()

        @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
        def spa(path: str) -> FileResponse:  # sync: FastAPI runs it in a worker thread
            file = (root / path).resolve()
            if path and file.is_file() and file.is_relative_to(root):
                return FileResponse(file)
            return FileResponse(root / "index.html")

    return app
