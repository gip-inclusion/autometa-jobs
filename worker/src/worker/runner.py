"""The agent loop. Spawns the claude CLI directly (no SDK), parses its
stream-json output, streams events back to the orchestrator, uploads the
artifact to S3, and reports a result before exiting.

Mirrors the autometa/matometa production pattern: env-var auth, JSON Lines
on stdout, stderr drained in parallel.
"""

import asyncio
import io
import json
import logging
import os
from datetime import datetime, timezone

import boto3

from worker.client import OrchestratorClient

log = logging.getLogger(__name__)

CLAUDE_BIN = os.environ.get("CLAUDE_CLI", "claude")


def _s3_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ.get("PIPOMETA_S3_ENDPOINT", "https://s3.fr-par.scw.cloud"),
        region_name="fr-par",
    )


async def _heartbeat_task(client: OrchestratorClient, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await client.heartbeat()
        except Exception:
            log.exception("heartbeat failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=30)
        except asyncio.TimeoutError:
            pass


def _read_prompt() -> str:
    input_uri = os.environ.get("PIPOMETA_INPUT_URI")
    if not input_uri:
        # No per-run input bundle: a self-contained pipeline carries its whole
        # task in the system prompt. Use it as the driving prompt so the CLI
        # has something to act on (--system-prompt alone never triggers work).
        explicit_default = os.environ.get("PIPOMETA_DEFAULT_PROMPT")
        if explicit_default:
            return explicit_default
        system_prompt = (os.environ.get("PIPOMETA_SYSTEM_PROMPT") or "").strip()
        if system_prompt:
            return system_prompt
        return "Say hello in 3 lines, in French. End with a haiku."
    if not input_uri.startswith("s3://"):
        raise ValueError(f"unsupported input uri scheme: {input_uri}")
    bucket, _, key = input_uri[5:].partition("/")
    s3 = _s3_client()
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
    try:
        return json.loads(body).get("prompt", body)
    except json.JSONDecodeError:
        return body


def _upload_artifact(text: str) -> str:
    bucket = os.environ["PIPOMETA_OUTPUT_BUCKET"]
    run_id = os.environ["PIPOMETA_RUN_ID"]
    pipeline = os.environ.get("PIPOMETA_PIPELINE_NAME", "unknown")
    ts = datetime.now(timezone.utc).strftime("%Y/%m/%d")
    key = f"runs/{ts}/{pipeline}/{run_id}/output.md"
    s3 = _s3_client()
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=io.BytesIO(text.encode("utf-8")),
        ContentType="text/markdown; charset=utf-8",
    )
    return f"s3://{bucket}/{key}"


async def _drain_stderr(stream: asyncio.StreamReader, sink: list[str]) -> None:
    while True:
        line = await stream.readline()
        if not line:
            return
        s = line.decode("utf-8", "replace").rstrip()
        if s:
            sink.append(s)
            if len(sink) > 200:
                del sink[: len(sink) - 200]


def _parse_assistant_blocks(event: dict) -> tuple[list[dict], list[str]]:
    """Return (event-payload-blocks, raw-text-fragments) from a claude 'assistant' event."""
    blocks: list[dict] = []
    text_parts: list[str] = []
    msg = event.get("message", {}) or {}
    for block in msg.get("content", []) or []:
        bt = block.get("type")
        if bt == "text":
            t = (block.get("text") or "").strip()
            if t:
                blocks.append({"type": "text", "text": t})
                text_parts.append(t)
        elif bt == "tool_use":
            blocks.append({
                "type": "tool_use",
                "name": block.get("name"),
                "id": block.get("id"),
                "input": block.get("input"),
            })
    return blocks, text_parts


async def run_pipeline(cancelled: asyncio.Event) -> int:
    client = OrchestratorClient()
    stop = asyncio.Event()
    hb = asyncio.create_task(_heartbeat_task(client, stop))

    seq = 1
    final_text_parts: list[str] = []
    cli_stderr: list[str] = []
    token_usage: dict | None = None
    exit_code = 0

    try:
        await client.event(seq, "started", {"pid": os.getpid()})
        seq += 1

        prompt = _read_prompt()
        await client.event(seq, "prompt_loaded", {"chars": len(prompt)})
        seq += 1

        system_prompt = (os.environ.get("PIPOMETA_SYSTEM_PROMPT") or "").strip() or (
            "You are a focused assistant. Be concise."
        )
        # Worker container runs as non-root `runner`, so the CLI accepts
        # --dangerously-skip-permissions. The container itself is the security
        # boundary (gVisor sandbox, ephemeral); inside, the agent should have
        # full freedom. Per-pipeline tool restrictions can be added via
        # PIPOMETA_ALLOWED_TOOLS if needed.
        cmd = [
            CLAUDE_BIN,
            "--output-format",
            "stream-json",
            "--verbose",
            "--system-prompt",
            system_prompt,
            "--dangerously-skip-permissions",
            "-p",
            prompt,
        ]
        if extra_tools := (os.environ.get("PIPOMETA_ALLOWED_TOOLS") or "").strip():
            cmd.extend(["--allowedTools", extra_tools])
        if max_turns := (os.environ.get("PIPOMETA_MAX_TURNS") or "").strip():
            cmd.extend(["--max-turns", max_turns])
        if model := (os.environ.get("PIPOMETA_MODEL") or "").strip():
            cmd.extend(["--model", model])
        # Pass the env through unchanged. Dispatch sets ANTHROPIC_API_KEY="" so
        # the CLI sees an explicit empty value rather than nothing — empty blocks
        # the API-key fallback path; unset would let some code paths re-resolve.
        env = dict(os.environ)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            limit=10 * 1024 * 1024,
        )
        stderr_task = asyncio.create_task(_drain_stderr(proc.stderr, cli_stderr))

        try:
            while True:
                if cancelled.is_set():
                    proc.terminate()
                    await client.event(seq, "cancelled", {})
                    seq += 1
                    exit_code = 130
                    break
                line = await proc.stdout.readline()
                if not line:
                    break
                line_str = line.decode("utf-8", "replace").strip()
                if not line_str:
                    continue
                try:
                    ev = json.loads(line_str)
                except json.JSONDecodeError:
                    await client.event(seq, "raw_line", {"line": line_str[:500]})
                    seq += 1
                    continue

                ev_type = ev.get("type")
                if ev_type == "assistant":
                    blocks, parts = _parse_assistant_blocks(ev)
                    if blocks:
                        await client.event(seq, "assistant_message", {"blocks": blocks})
                        seq += 1
                    final_text_parts.extend(parts)
                elif ev_type == "user":
                    msg = ev.get("message", {}) or {}
                    for block in msg.get("content", []) or []:
                        if block.get("type") == "tool_result":
                            content = block.get("content", "")
                            await client.event(
                                seq,
                                "tool_result",
                                {
                                    "tool_use_id": block.get("tool_use_id"),
                                    "content": str(content)[:2000],
                                },
                            )
                            seq += 1
                elif ev_type == "result":
                    token_usage = {
                        "duration_ms": ev.get("duration_ms"),
                        "duration_api_ms": ev.get("duration_api_ms"),
                        "num_turns": ev.get("num_turns"),
                        "total_cost_usd": ev.get("total_cost_usd"),
                        "usage": ev.get("usage"),
                        "subtype": ev.get("subtype"),
                    }
                    await client.event(seq, "result", token_usage)
                    seq += 1
                elif ev_type == "system":
                    await client.event(seq, "system", {"subtype": ev.get("subtype"), "message": ev.get("message")})
                    seq += 1
                elif ev_type == "error":
                    await client.event(seq, "claude_error", ev)
                    seq += 1
                else:
                    await client.event(seq, "message", {"kind": ev_type})
                    seq += 1
        finally:
            await proc.wait()
            try:
                await asyncio.wait_for(stderr_task, timeout=2)
            except asyncio.TimeoutError:
                pass

        if proc.returncode != 0 and exit_code == 0:
            await client.event(
                seq,
                "error",
                {
                    "type": "ProcessError",
                    "exit_code": proc.returncode,
                    "cli_stderr": "\n".join(cli_stderr)[-3000:],
                },
            )
            seq += 1
            return 1

        artifact = "\n\n".join(final_text_parts).strip() or "(empty)"
        output_uri = _upload_artifact(artifact)
        await client.result(output_uri=output_uri, summary=artifact[:280], token_usage=token_usage)
        return exit_code

    except Exception as e:
        log.exception("pipeline failed")
        try:
            await client.event(
                seq,
                "error",
                {
                    "type": type(e).__name__,
                    "message": str(e)[:2000],
                    "cli_stderr": "\n".join(cli_stderr)[-3000:],
                },
            )
        except Exception:
            pass
        return 1
    finally:
        stop.set()
        await hb
        await client.aclose()
