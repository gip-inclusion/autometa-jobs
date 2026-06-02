import json
import os
import sys

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
