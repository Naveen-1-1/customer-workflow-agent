"""Operational endpoints (outside /api): /healthz, /readyz and /metrics."""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from customer_workflow_agent.obs.metrics import READY

log = logging.getLogger(__name__)
router = APIRouter()


async def readiness(svc) -> dict[str, bool]:
    """Can the app serve customers? Never calls the LLM (that would use up the rate limit)."""
    checks: dict[str, bool] = {}
    runner = svc.runner
    try:
        checks["store"] = bool(svc.store.list_all_product_types())
    except Exception:
        log.exception("readyz: store check failed")
        checks["store"] = False
    try:
        await runner.appdb.ping()
        checks["chat_db"] = True
    except Exception:
        log.exception("readyz: chat DB check failed")
        checks["chat_db"] = False
    try:
        await runner.saver.aget_tuple({"configurable": {"thread_id": "__readyz__"}})
        checks["checkpoints"] = True
    except Exception:
        log.exception("readyz: checkpoint check failed")
        checks["checkpoints"] = False
    checks["llm_configured"] = svc.llm_configured
    READY.set(1 if all(checks.values()) else 0)
    return checks


@router.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    checks = await readiness(request.app.state.services)
    ready = all(checks.values())
    return JSONResponse({"ready": ready, "checks": checks}, status_code=200 if ready else 503)


@router.get("/metrics")
async def metrics(request: Request) -> Response:
    svc = request.app.state.services
    await readiness(svc)  # keeps agent_ready current for alerts
    await svc.runner.open_chats()  # chats go idle without any event, so recount on each scrape
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
