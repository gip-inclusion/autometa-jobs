from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.auth import require_api_key
from orchestrator.db import get_session
from orchestrator.dispatch import _dispatch_one
from orchestrator.models import Pipeline, Run, RunStatus
from orchestrator.schemas import PipelineCreate, PipelineRead, PipelineUpdate, RunRead, RunTrigger

router = APIRouter(prefix="/pipelines", tags=["pipelines"], dependencies=[Depends(require_api_key)])


@router.post("", response_model=PipelineRead, status_code=201)
async def create_pipeline(body: PipelineCreate, session: AsyncSession = Depends(get_session)) -> Pipeline:
    pipeline = Pipeline(name=body.name, system_prompt=body.system_prompt, config=body.config)
    session.add(pipeline)
    await session.commit()
    await session.refresh(pipeline)
    return pipeline


@router.get("", response_model=list[PipelineRead])
async def list_pipelines(session: AsyncSession = Depends(get_session)) -> list[Pipeline]:
    rows = await session.execute(select(Pipeline).order_by(Pipeline.created_at.desc()))
    return list(rows.scalars())


@router.get("/{pipeline_id}", response_model=PipelineRead)
async def get_pipeline(pipeline_id: UUID, session: AsyncSession = Depends(get_session)) -> Pipeline:
    pipeline = await session.get(Pipeline, pipeline_id)
    if pipeline is None:
        raise HTTPException(404, "pipeline not found")
    return pipeline


@router.patch("/{pipeline_id}", response_model=PipelineRead)
async def update_pipeline(
    pipeline_id: UUID, body: PipelineUpdate, session: AsyncSession = Depends(get_session)
) -> Pipeline:
    pipeline = await session.get(Pipeline, pipeline_id)
    if pipeline is None:
        raise HTTPException(404, "pipeline not found")
    if body.name is not None:
        pipeline.name = body.name
    if body.system_prompt is not None:
        pipeline.system_prompt = body.system_prompt
    if body.config is not None:
        pipeline.config = body.config
    await session.commit()
    await session.refresh(pipeline)
    return pipeline


@router.post("/{pipeline_id}/runs", response_model=RunRead, status_code=201)
async def trigger_run(pipeline_id: UUID, body: RunTrigger, session: AsyncSession = Depends(get_session)) -> Run:
    pipeline = await session.get(Pipeline, pipeline_id)
    if pipeline is None:
        raise HTTPException(404, "pipeline not found")

    if body.idempotency_key:
        existing = await session.execute(
            select(Run).where(Run.pipeline_id == pipeline_id, Run.idempotency_key == body.idempotency_key)
        )
        prior = existing.scalar_one_or_none()
        if prior is not None:
            return prior

    run = Run(
        pipeline_id=pipeline_id,
        status=RunStatus.queued,
        input_uri=body.input_uri,
        idempotency_key=body.idempotency_key,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)

    # Inline best-effort dispatch — gives us low-latency starts even if cron
    # is the steady-state driver.
    try:
        await _dispatch_one()
    except Exception:
        pass

    await session.refresh(run)
    return run
