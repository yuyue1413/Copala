"""LifeEvent CLI commands."""

import asyncio

import typer
from rich.console import Console
from rich.table import Table

from copala.cli.runtime import life_event_manager_from_config, load_runtime_config

console = Console()
life_events_app = typer.Typer(help="Manage personal life events")


@life_events_app.command("list")
def life_events_list(
    limit: int = typer.Option(20, "--limit", "-n", help="Max events to show"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
):
    """List recorded personal life events."""
    cfg = load_runtime_config(config, workspace)
    events = life_event_manager_from_config(cfg).list_events(limit=limit)
    table = Table(title="Life Events")
    table.add_column("Status", style="green")
    table.add_column("Type", style="cyan")
    table.add_column("Start")
    table.add_column("Title")
    table.add_column("Source")
    for event in events:
        table.add_row(
            str(event.get("status") or ""),
            str(event.get("type") or ""),
            str(event.get("start") or ""),
            str(event.get("title") or ""),
            str(event.get("source_ref") or ""),
        )
    console.print(table if events else "[yellow]No life events recorded.[/yellow]")


@life_events_app.command("reprocess-email")
def life_events_reprocess_email(
    limit: int = typer.Option(20, "--limit", "-n", help="Max unread emails to process"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
):
    """Process unread mailbox messages through the life-event manager."""
    cfg = load_runtime_config(config, workspace)
    result = asyncio.run(life_event_manager_from_config(cfg).patrol_email(limit=limit))
    console.print(result)
