import asyncio
import logging
import os
import pathlib
import signal
import sys

from worker.runner import run_pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s :: %(message)s")
log = logging.getLogger("worker")


def _ensure_oauth_only() -> None:
    """Belt-and-braces: ensure the SDK uses the OAuth token, never an API key."""
    if not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        log.error("CLAUDE_CODE_OAUTH_TOKEN missing — refusing to start")
        sys.exit(2)
    if os.environ.get("ANTHROPIC_API_KEY"):
        # Wipe both common env names so the SDK can't fall back.
        log.warning("ANTHROPIC_API_KEY was set; clearing it for this run")
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)


def _bypass_onboarding() -> None:
    """Mark onboarding complete so the CLI skips the interactive wizard.

    Auth itself is handled via the CLAUDE_CODE_OAUTH_TOKEN env var that
    Scaleway injects at job start. Same pattern matometa/autometa use in
    production on Scalingo.
    """
    home = pathlib.Path(os.environ.get("HOME", "/root"))
    home.mkdir(parents=True, exist_ok=True)
    (home / ".claude.json").write_text('{"hasCompletedOnboarding":true}')
    log.info("wrote %s/.claude.json (onboarding bypass)", home)


async def _amain() -> int:
    _ensure_oauth_only()
    _bypass_onboarding()

    cancelled = asyncio.Event()

    def _on_term(*_):
        log.info("SIGTERM received, requesting cooperative cancel")
        cancelled.set()

    signal.signal(signal.SIGTERM, _on_term)

    return await run_pipeline(cancelled=cancelled)


def main() -> None:
    sys.exit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
