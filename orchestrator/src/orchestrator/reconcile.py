"""Reconciliation step.

Three cleanup passes, all driven by the per-minute cron via /admin/tick:

1. Stale heartbeat: a run claims to be running but hasn't pinged in >N seconds.
   Poll Scaleway; if Scaleway says it's terminal, mirror that locally.
2. Orphan starting: a run flipped to `starting` but never got a job_run_id
   (orchestrator crashed mid-dispatch). Mark failed after a short grace window.
3. Stale hmac_keys: a run reached terminal status but its hmac_key is still set.
   Clear it after a short grace so a worker that's still finishing can land
   its last events / cancellation notice without a 403.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from orchestrator import scaleway
from orchestrator.config import settings
from orchestrator.db import SessionLocal
from orchestrator.models import TERMINAL_STATUSES, Run, RunStatus

log = logging.getLogger(__name__)

# Grace windows (seconds)
ORPHAN_STARTING_AFTER = 60  # dispatch should complete in well under this
STALE_HMAC_AFTER = 60  # how long after terminal we keep the key around

# Map Scaleway job-run terminal states → our RunStatus.
SCW_STATE_MAP = {
    "succeeded": RunStatus.completed,
    "failed": RunStatus.failed,
    "canceled": RunStatus.cancelled,
    "internal_error": RunStatus.failed,
}


async def _reconcile_stale_heartbeats() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.heartbeat_stale_seconds)

    async with SessionLocal() as session:
        rows = await session.execute(
            select(Run).where(
                Run.status.in_([RunStatus.starting, RunStatus.running]),
                Run.last_heartbeat_at.is_(None) | (Run.last_heartbeat_at < cutoff),
                Run.scaleway_job_run_id.is_not(None),
            )
        )
        stale = list(rows.scalars())

    for run in stale:
        try:
            jr = await scaleway.get_job_run(run.scaleway_job_run_id)  # type: ignore[arg-type]
        except Exception:
            log.exception("failed to poll scaleway job run %s", run.scaleway_job_run_id)
            continue

        terminal = SCW_STATE_MAP.get(jr.get("state") or "")
        if terminal is None:
            continue  # still running, give it more time

        async with SessionLocal() as session:
            r = await session.get(Run, run.id)
            if r is None or r.status in TERMINAL_STATUSES:
                continue
            r.status = terminal
            r.error_text = r.error_text or f"reconciled from scaleway state '{jr.get('state')}'"
            r.finished_at = datetime.now(timezone.utc)
            await session.commit()
            log.warning("reconciled run %s as %s", r.id, terminal)


async def _reconcile_orphan_starting() -> None:
    """Catch runs whose dispatch crashed before start_job returned."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=ORPHAN_STARTING_AFTER)

    async with SessionLocal() as session:
        rows = await session.execute(
            select(Run).where(
                Run.status == RunStatus.starting,
                Run.scaleway_job_run_id.is_(None),
                Run.started_at.is_not(None),
                Run.started_at < cutoff,
            )
        )
        orphans = list(rows.scalars())

    for run in orphans:
        async with SessionLocal() as session:
            r = await session.get(Run, run.id)
            if r is None or r.status != RunStatus.starting or r.scaleway_job_run_id:
                continue
            r.status = RunStatus.failed
            r.error_text = r.error_text or "orphan: dispatch crashed before scaleway start_job returned"
            r.finished_at = datetime.now(timezone.utc)
            await session.commit()
            log.warning("reconciled orphan starting run %s as failed", r.id)


async def _clear_stale_hmac_keys() -> None:
    """Erase hmac_key on terminal runs after a grace window.

    Cancel paths leave the key in place so the worker can land a final
    `cancelled` event during the SIGTERM grace period.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=STALE_HMAC_AFTER)

    async with SessionLocal() as session:
        rows = await session.execute(
            select(Run).where(
                Run.status.in_(list(TERMINAL_STATUSES)),
                Run.hmac_key.is_not(None),
                Run.finished_at.is_not(None),
                Run.finished_at < cutoff,
            )
        )
        cleared = 0
        for r in rows.scalars():
            r.hmac_key = None
            cleared += 1
        if cleared:
            await session.commit()
            log.info("cleared hmac_key on %d terminal run(s)", cleared)


async def _reconcile_once() -> None:
    await _reconcile_stale_heartbeats()
    await _reconcile_orphan_starting()
    await _clear_stale_hmac_keys()
