"""HMAC-signed callbacks from the worker back to the orchestrator.

Signed payload is `run_id || ts || body`, separated by \\n. The orchestrator
rejects requests whose timestamp drifts more than 5 minutes from server time,
which closes the replay window beyond what HTTPS already gives us.
"""

import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)


class OrchestratorClient:
    def __init__(self) -> None:
        self.base_url = os.environ["PIPOMETA_ORCHESTRATOR_URL"].rstrip("/")
        self.run_id = os.environ["PIPOMETA_RUN_ID"]
        self.hmac_key = os.environ["PIPOMETA_RUN_HMAC_KEY"].encode()
        self.client = httpx.AsyncClient(timeout=15)

    async def aclose(self) -> None:
        await self.client.aclose()

    def _sign(self, ts: str, body: bytes) -> str:
        payload = self.run_id.encode() + b"\n" + ts.encode() + b"\n" + body
        return hmac.new(self.hmac_key, payload, hashlib.sha256).hexdigest()

    async def _send(self, method: str, path: str, payload: dict | None = None) -> httpx.Response:
        body = json.dumps(payload or {}, separators=(",", ":")).encode()
        ts = str(int(time.time()))
        headers = {
            "Content-Type": "application/json",
            "X-Run-Timestamp": ts,
            "X-Run-Signature": self._sign(ts, body),
        }
        url = f"{self.base_url}/runs/{self.run_id}{path}"
        r = await self.client.request(method, url, content=body, headers=headers)
        if r.status_code >= 400:
            log.error("orchestrator %s %s -> %s: %s", method, path, r.status_code, r.text[:200])
        return r

    async def heartbeat(self) -> None:
        await self._send("PUT", "/heartbeat")

    async def event(self, seq: int, event_type: str, payload: dict[str, Any]) -> None:
        await self._send("POST", "/events", {"seq": seq, "event_type": event_type, "payload": payload})

    async def result(self, output_uri: str, summary: str | None, token_usage: dict | None) -> None:
        await self._send(
            "PUT",
            "/result",
            {"output_uri": output_uri, "summary": summary, "token_usage": token_usage},
        )
