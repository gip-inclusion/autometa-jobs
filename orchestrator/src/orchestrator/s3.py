"""Read-side S3 access for run artifacts.

The worker writes each run's artifact to the Scaleway bucket; the orchestrator
owns the credentials (it injects them into the worker at dispatch) and is the
single authenticated channel consumers use to read results back. boto3 is sync,
so callers wrap these in ``asyncio.to_thread``.
"""

import boto3

from orchestrator.config import settings


def parse_s3_uri(uri: str) -> tuple[str, str]:
    """Split ``s3://bucket/key/path`` into ``(bucket, key)``."""
    if not uri.startswith("s3://"):
        raise ValueError(f"not an s3 uri: {uri}")
    bucket, _, key = uri[len("s3://"):].partition("/")
    if not bucket or not key:
        raise ValueError(f"incomplete s3 uri: {uri}")
    return bucket, key


def _client():
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        region_name=settings.scaleway_region,
        aws_access_key_id=settings.scaleway_access_key,
        aws_secret_access_key=settings.scaleway_secret_key,
    )


def read_object(bucket: str, key: str) -> tuple[bytes, str]:
    """Return ``(body, content_type)`` for an object."""
    obj = _client().get_object(Bucket=bucket, Key=key)
    content_type = obj.get("ContentType") or "application/octet-stream"
    return obj["Body"].read(), content_type


def presign_get(bucket: str, key: str, expires_in: int, *, filename: str | None = None) -> str:
    """A short-lived GET URL. ``filename`` forces a download via Content-Disposition."""
    params: dict = {"Bucket": bucket, "Key": key}
    if filename:
        params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
    return _client().generate_presigned_url("get_object", Params=params, ExpiresIn=expires_in)
