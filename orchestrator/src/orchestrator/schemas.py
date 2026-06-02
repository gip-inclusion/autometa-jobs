from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from orchestrator.models import RunStatus


class PipelineCreate(BaseModel):
    name: str
    system_prompt: str
    config: dict = Field(default_factory=dict)


class PipelineUpdate(BaseModel):
    name: str | None = None
    system_prompt: str | None = None
    config: dict | None = None


class PipelineRead(BaseModel):
    id: UUID
    name: str
    system_prompt: str
    config: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class RunTrigger(BaseModel):
    input_uri: str | None = None
    idempotency_key: str | None = None
    parameters: dict = Field(default_factory=dict)


class RunRead(BaseModel):
    id: UUID
    pipeline_id: UUID
    status: RunStatus
    scaleway_job_run_id: str | None
    input_uri: str | None
    output_uri: str | None
    summary: str | None
    error_text: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    last_heartbeat_at: datetime | None

    model_config = {"from_attributes": True}


class EventIn(BaseModel):
    seq: int
    event_type: str
    payload: dict = Field(default_factory=dict)


class ResultIn(BaseModel):
    output_uri: str
    summary: str | None = None
    token_usage: dict | None = None


class HeartbeatIn(BaseModel):
    pass
