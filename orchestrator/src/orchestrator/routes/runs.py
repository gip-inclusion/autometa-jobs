import asyncio
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator import s3, scaleway
from orchestrator.auth import require_api_key, verify_run_hmac
from orchestrator.db import get_session
from orchestrator.models import TERMINAL_STATUSES, Run, RunEvent, RunStatus
from orchestrator.schemas import EventIn, ResultIn, RunRead

# Bounds for presigned-URL lifetime, in seconds.
_PRESIGN_DEFAULT = 3600
_PRESIGN_MAX = 86400

# Two routers: user-facing (api key) and worker-facing (HMAC).
api_router = APIRouter(prefix="/runs", tags=["runs"], dependencies=[Depends(require_api_key)])
worker_router = APIRouter(prefix="/runs", tags=["runs-internal"])


@api_router.get("", response_model=list[RunRead])
async def list_runs(
    pipeline_id: UUID | None = None,
    status: RunStatus | None = None,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
) -> list[Run]:
    """List runs newest-first, optionally filtered by pipeline and/or status.

    Lets callers (e.g. the port-loop driver) check for already active runs
    before triggering, so they can stay idempotent without piling duplicates.
    """
    query = select(Run).order_by(Run.created_at.desc()).limit(min(max(limit, 1), 200))
    if pipeline_id is not None:
        query = query.where(Run.pipeline_id == pipeline_id)
    if status is not None:
        query = query.where(Run.status == status)
    rows = await session.execute(query)
    return list(rows.scalars())


@api_router.get("/{run_id}", response_model=RunRead)
async def get_run(run_id: UUID, session: AsyncSession = Depends(get_session)) -> Run:
    run = await session.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    return run


@api_router.get("/{run_id}/events")
async def get_run_events(run_id: UUID, session: AsyncSession = Depends(get_session)) -> list[dict]:
    rows = await session.execute(select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.seq))
    return [
        {"seq": e.seq, "event_type": e.event_type, "payload": e.payload, "created_at": e.created_at}
        for e in rows.scalars()
    ]


@api_router.get("/{run_id}/output")
async def get_run_output(
    run_id: UUID,
    presign: bool = False,
    expires_in: int = _PRESIGN_DEFAULT,
    session: AsyncSession = Depends(get_session),
):
    """The run's full artifact.

    Default: stream the content (the agent reads it directly). With
    ``?presign=1``: return ``{"url", "expires_in"}`` — a short-lived S3 GET URL
    that forces a download, for the UI to link. Either way the artifact bytes
    never need credentials on the consumer side; the orchestrator owns them.
    """
    run = await session.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    if not run.output_uri:
        raise HTTPException(404, "run has no artifact")
    try:
        bucket, key = s3.parse_s3_uri(run.output_uri)
    except ValueError as e:
        raise HTTPException(500, f"bad output_uri: {e}") from e

    if presign:
        ttl = min(max(expires_in, 60), _PRESIGN_MAX)
        url = await asyncio.to_thread(s3.presign_get, bucket, key, ttl, filename="output.md")
        return {"url": url, "expires_in": ttl}

    try:
        body, content_type = await asyncio.to_thread(s3.read_object, bucket, key)
    except Exception as e:
        raise HTTPException(502, f"artifact fetch failed: {e}") from e
    return Response(content=body, media_type=content_type)


@api_router.post("/{run_id}/cancel", response_model=RunRead)
async def cancel_run(run_id: UUID, session: AsyncSession = Depends(get_session)) -> Run:
    run = await session.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    if run.status in TERMINAL_STATUSES:
        return run
    if run.scaleway_job_run_id:
        try:
            await scaleway.stop_job_run(run.scaleway_job_run_id)
        except Exception as e:
            raise HTTPException(502, f"scaleway stop failed: {e}") from e
    run.status = RunStatus.cancelled
    run.finished_at = datetime.now(timezone.utc)
    # Keep hmac_key set so the worker's last events (including a final
    # `cancelled` event during the SIGTERM grace) still authenticate. The
    # reconciliation loop clears it after STALE_HMAC_AFTER seconds.
    await session.commit()
    await session.refresh(run)
    return run


async def _authed_run(
    run_id: UUID,
    request: Request,
    x_run_signature: str,
    x_run_timestamp: str,
    session: AsyncSession,
) -> tuple[Run, bytes]:
    body = await request.body()
    run = await session.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    verify_run_hmac(x_run_signature, run.hmac_key, str(run.id), x_run_timestamp, body)
    return run, body


@worker_router.put("/{run_id}/heartbeat", status_code=204)
async def heartbeat(
    run_id: UUID,
    request: Request,
    x_run_signature: str = Header(...),
    x_run_timestamp: str = Header(...),
    session: AsyncSession = Depends(get_session),
) -> None:
    run, _ = await _authed_run(run_id, request, x_run_signature, x_run_timestamp, session)
    run.last_heartbeat_at = datetime.now(timezone.utc)
    await session.commit()


@worker_router.post("/{run_id}/events", status_code=204)
async def append_event(
    run_id: UUID,
    body: EventIn,
    request: Request,
    x_run_signature: str = Header(...),
    x_run_timestamp: str = Header(...),
    session: AsyncSession = Depends(get_session),
) -> None:
    run, _ = await _authed_run(run_id, request, x_run_signature, x_run_timestamp, session)
    # Idempotent insert: a worker retry on the same (run_id, seq) is a no-op.
    stmt = (
        pg_insert(RunEvent)
        .values(
            run_id=run.id,
            seq=body.seq,
            event_type=body.event_type,
            payload=body.payload,
        )
        .on_conflict_do_nothing(index_elements=["run_id", "seq"])
    )
    await session.execute(stmt)
    # State transition only if the run isn't already in a terminal state —
    # avoids an in-flight `quota_hit` clobbering an earlier `cancelled`.
    if body.event_type == "quota_hit" and run.status not in TERMINAL_STATUSES:
        run.status = RunStatus.quota_blocked
        run.finished_at = datetime.now(timezone.utc)
    await session.commit()


@worker_router.put("/{run_id}/result", response_model=RunRead)
async def submit_result(
    run_id: UUID,
    body: ResultIn,
    request: Request,
    x_run_signature: str = Header(...),
    x_run_timestamp: str = Header(...),
    session: AsyncSession = Depends(get_session),
) -> Run:
    run, _ = await _authed_run(run_id, request, x_run_signature, x_run_timestamp, session)
    # Race guard: a cancel that landed in the same window must win. Same for
    # quota_blocked or any other terminal status. We still record the artifact
    # info, but we don't flip the status back to `completed`.
    if run.status not in TERMINAL_STATUSES:
        run.status = RunStatus.completed
        run.finished_at = datetime.now(timezone.utc)
    if not run.output_uri:
        run.output_uri = body.output_uri
    if not run.summary:
        run.summary = body.summary
    if not run.token_usage:
        run.token_usage = body.token_usage
    await session.commit()
    await session.refresh(run)
    return run
