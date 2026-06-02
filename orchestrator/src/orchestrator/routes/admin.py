"""Cron-driven endpoint to advance run state.

Scaleway Serverless Containers freeze CPU between requests, so the
asyncio background loops in `dispatch.py` and `reconcile.py` can't be
relied on. A Scaleway Container cron POSTs `{"action":"tick", "token":...}`
to `/` (or to `/admin/tick`) every minute and we do one dispatch step
plus one reconciliation step.
"""

import hmac
import logging

from fastapi import APIRouter, HTTPException

from orchestrator.config import settings
from orchestrator.dispatch import _dispatch_one
from orchestrator.reconcile import _reconcile_once

log = logging.getLogger(__name__)
router = APIRouter()


def _check_token(token: str | None) -> None:
    if not settings.cron_secret:
        return
    if not token or not hmac.compare_digest(token, settings.cron_secret):
        raise HTTPException(403, "bad cron token")


async def _tick(burst: int = 5) -> dict:
    """Run dispatch + reconcile. `burst` lets us drain a small queue per tick."""
    dispatched = 0
    for _ in range(burst):
        try:
            did = await _dispatch_one()
        except Exception:
            log.exception("dispatch_one crashed")
            break
        if not did:
            break
        dispatched += 1
    try:
        await _reconcile_once()
    except Exception:
        log.exception("reconcile_once crashed")
    return {"ok": True, "dispatched": dispatched}


@router.post("/admin/tick")
async def admin_tick(payload: dict | None = None) -> dict:
    payload = payload or {}
    _check_token(payload.get("token"))
    return await _tick(burst=int(payload.get("burst", 5)))


# Scaleway cron sends POST to "/" with the configured args as JSON body.
# Routing the same handler at "/" lets us bind the cron without depending on path.
@router.post("/")
async def root_post(payload: dict | None = None) -> dict:
    payload = payload or {}
    if payload.get("action") != "tick":
        return {"ok": True}
    _check_token(payload.get("token"))
    return await _tick(burst=int(payload.get("burst", 5)))
