import json
import os
import sys
from typing import IO

import click
import httpx
from rich.console import Console
from rich.table import Table

console = Console()


def _client() -> httpx.Client:
    base = os.environ.get("PIPOMETA_URL")
    api_key = os.environ.get("PIPOMETA_API_KEY")
    if not base or not api_key:
        console.print("[red]PIPOMETA_URL and PIPOMETA_API_KEY must be set[/red]")
        sys.exit(2)
    return httpx.Client(base_url=base.rstrip("/"), headers={"Authorization": f"Bearer {api_key}"}, timeout=30)


def _resolve(inline: str | None, file: IO[str] | None) -> str | None:
    if inline is not None:
        return inline
    if file is not None:
        return file.read()
    return None


def _parse_config(raw: str | None) -> dict | None:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"invalid config JSON: {exc}")


@click.group()
def cli() -> None:
    """autometa-jobs control plane."""


@cli.command("pipelines")
def list_pipelines() -> None:
    with _client() as c:
        r = c.get("/pipelines")
        r.raise_for_status()
    table = Table("name", "id", "created_at")
    for p in r.json():
        table.add_row(p["name"], p["id"], p["created_at"])
    console.print(table)


@cli.command("pipeline-get")
@click.argument("pipeline_id")
def pipeline_get(pipeline_id: str) -> None:
    """Show a single pipeline."""
    with _client() as c:
        r = c.get(f"/pipelines/{pipeline_id}")
        r.raise_for_status()
    console.print_json(json.dumps(r.json()))


@cli.command("pipeline-create")
@click.option("--name", required=True)
@click.option("--system-prompt", help="Consigne inline (ou --system-prompt-file)")
@click.option("--system-prompt-file", type=click.File("r"), help="Fichier contenant la consigne")
@click.option("--config", help="Config JSON inline (ou --config-file)")
@click.option("--config-file", type=click.File("r"), help="Fichier JSON de config")
def pipeline_create(
    name: str,
    system_prompt: str | None,
    system_prompt_file: IO[str] | None,
    config: str | None,
    config_file: IO[str] | None,
) -> None:
    """Create a pipeline."""
    prompt = _resolve(system_prompt, system_prompt_file)
    if prompt is None:
        raise click.ClickException("provide --system-prompt or --system-prompt-file")
    body: dict = {"name": name, "system_prompt": prompt}
    cfg = _parse_config(_resolve(config, config_file))
    if cfg is not None:
        body["config"] = cfg
    with _client() as c:
        r = c.post("/pipelines", json=body)
        r.raise_for_status()
    console.print_json(json.dumps(r.json()))


@cli.command("pipeline-update")
@click.argument("pipeline_id")
@click.option("--name")
@click.option("--system-prompt", help="Consigne inline (ou --system-prompt-file)")
@click.option("--system-prompt-file", type=click.File("r"), help="Fichier contenant la consigne")
@click.option("--config", help="Config JSON inline (ou --config-file)")
@click.option("--config-file", type=click.File("r"), help="Fichier JSON de config")
def pipeline_update(
    pipeline_id: str,
    name: str | None,
    system_prompt: str | None,
    system_prompt_file: IO[str] | None,
    config: str | None,
    config_file: IO[str] | None,
) -> None:
    """Update name/system_prompt/config of a pipeline."""
    body: dict = {}
    if name is not None:
        body["name"] = name
    prompt = _resolve(system_prompt, system_prompt_file)
    if prompt is not None:
        body["system_prompt"] = prompt
    cfg = _parse_config(_resolve(config, config_file))
    if cfg is not None:
        body["config"] = cfg
    if not body:
        raise click.ClickException("nothing to update: pass --name, --system-prompt(-file) or --config(-file)")
    with _client() as c:
        r = c.patch(f"/pipelines/{pipeline_id}", json=body)
        r.raise_for_status()
    console.print_json(json.dumps(r.json()))


@cli.command("trigger")
@click.argument("pipeline_id")
@click.option("--input-uri", default=None)
@click.option("--idempotency-key", default=None)
def trigger(pipeline_id: str, input_uri: str | None, idempotency_key: str | None) -> None:
    with _client() as c:
        r = c.post(
            f"/pipelines/{pipeline_id}/runs",
            json={"input_uri": input_uri, "idempotency_key": idempotency_key},
        )
        r.raise_for_status()
    console.print_json(json.dumps(r.json()))


@cli.command("status")
@click.argument("run_id")
def status(run_id: str) -> None:
    with _client() as c:
        r = c.get(f"/runs/{run_id}")
        r.raise_for_status()
    console.print_json(json.dumps(r.json()))


@cli.command("events")
@click.argument("run_id")
def events(run_id: str) -> None:
    with _client() as c:
        r = c.get(f"/runs/{run_id}/events")
        r.raise_for_status()
    for e in r.json():
        console.print(f"[{e['seq']:03d}] {e['event_type']}", style="cyan", end=" ")
        console.print(json.dumps(e["payload"])[:200])


@cli.command("cancel")
@click.argument("run_id")
def cancel(run_id: str) -> None:
    with _client() as c:
        r = c.post(f"/runs/{run_id}/cancel")
        r.raise_for_status()
    console.print_json(json.dumps(r.json()))


if __name__ == "__main__":
    cli()
