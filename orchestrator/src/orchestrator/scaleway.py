"""Scaleway API access via the official Python SDK.

The SDK is sync; we wrap calls in `asyncio.to_thread`. With dispatch concurrency=1
the orchestrator never has more than one outbound Scaleway call in flight, so the
threadpool overhead is trivial.

Why the SDK rather than handcrafted httpx: typed requests/responses, retries
handled, future API version migrations are a dep bump.
"""

import asyncio
import base64
from functools import lru_cache
from typing import Any

from scaleway import Client
from scaleway.jobs.v1alpha1 import JobsV1Alpha1API
from scaleway.secret.v1beta1 import SecretV1Beta1API

from orchestrator.config import settings


def _state(value: Any) -> str:
    """Coerce a Scaleway enum (or string) to its lowercase wire value."""
    return getattr(value, "value", str(value)).lower()


@lru_cache(maxsize=1)
def _client() -> Client:
    return Client(
        access_key=settings.scaleway_access_key,
        secret_key=settings.scaleway_secret_key,
        default_project_id=settings.scaleway_project_id,
        default_region=settings.scaleway_region,
    )


@lru_cache(maxsize=1)
def _jobs() -> JobsV1Alpha1API:
    return JobsV1Alpha1API(_client())


@lru_cache(maxsize=1)
def _secrets() -> SecretV1Beta1API:
    return SecretV1Beta1API(_client())


async def start_job(job_definition_id: str, environment: dict[str, str]) -> dict:
    """Start one run of the given Job definition. Returns a dict view of the first JobRun."""

    def _go() -> dict:
        resp = _jobs().start_job_definition(
            job_definition_id=job_definition_id,
            environment_variables=environment,
        )
        if not resp.job_runs:
            raise RuntimeError("start_job_definition returned no job_runs")
        jr = resp.job_runs[0]
        return {"id": jr.id, "state": _state(jr.state)}

    return await asyncio.to_thread(_go)


async def get_job_run(job_run_id: str) -> dict:
    def _go() -> dict:
        jr = _jobs().get_job_run(job_run_id=job_run_id)
        return {
            "id": jr.id,
            "state": _state(jr.state),
            "exit_code": jr.exit_code,
            "error_message": jr.error_message,
        }

    return await asyncio.to_thread(_go)


async def stop_job_run(job_run_id: str) -> None:
    def _go() -> None:
        _jobs().stop_job_run(job_run_id=job_run_id)

    await asyncio.to_thread(_go)


async def access_secret_value(secret_id: str, revision: str = "latest_enabled") -> str:
    """Fetch and decode a Secret Manager value as plaintext. Caller must not log it."""

    def _go() -> str:
        resp = _secrets().access_secret_version(secret_id=secret_id, revision=revision)
        return base64.b64decode(resp.data).decode("utf-8").strip()

    return await asyncio.to_thread(_go)
