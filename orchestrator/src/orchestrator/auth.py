import hashlib
import hmac
import secrets
import time

from fastapi import Header, HTTPException, status

from orchestrator.config import settings

# Worker callbacks must arrive within this window of their stamped time.
# Tight enough to defeat replay of captured requests, loose enough to absorb
# clock skew between Scaleway's job runner and the orchestrator container.
HMAC_MAX_SKEW_SECONDS = 300


def require_api_key(authorization: str = Header(default="")) -> None:
    expected = f"Bearer {settings.api_key}"
    if not hmac.compare_digest(authorization, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid api key")


def new_hmac_key() -> str:
    return secrets.token_urlsafe(32)


def verify_run_hmac(provided: str, expected: str | None, run_id: str, timestamp: str, body: bytes) -> None:
    """Verify a worker callback. Signed payload is `run_id || ts || body`, separated by \\n."""
    if not expected:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "run has no active hmac key")

    # Reject stale or future-skewed timestamps before doing any crypto.
    try:
        ts = int(timestamp)
    except TypeError, ValueError:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "bad timestamp") from None
    skew = abs(int(time.time()) - ts)
    if skew > HMAC_MAX_SKEW_SECONDS:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"stale request (skew={skew}s)")

    payload = run_id.encode() + b"\n" + timestamp.encode() + b"\n" + body
    mac = hmac.new(expected.encode(), payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, provided):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "bad signature")
