"""Context-aware reminder CLI commands."""

import asyncio

import typer
from rich.console import Console

from copala.cli.runtime import load_runtime_config, smart_reminder_service_from_config

console = Console()
smart_reminders_app = typer.Typer(help="Manage context-aware smart reminders")


@smart_reminders_app.command("status")
def smart_reminders_status(
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
):
    """Show smart reminder and Amap configuration status."""
    cfg = load_runtime_config(config, workspace)
    console.print(smart_reminder_service_from_config(cfg, include_life_events=False).status())


@smart_reminders_app.command("check")
def smart_reminders_check(
    days: int = typer.Option(2, "--days", "-d", help="Days to look ahead"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
):
    """Check upcoming weather, traffic and deadline risks."""
    cfg = load_runtime_config(config, workspace)
    service = smart_reminder_service_from_config(cfg)
    risks = asyncio.run(service.check_risks(days=days))
    console.print(service.format_risks(risks))


@smart_reminders_app.command("patrol")
def smart_reminders_patrol(
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
):
    """Run one smart-reminder patrol pass."""
    cfg = load_runtime_config(config, workspace)
    console.print(asyncio.run(smart_reminder_service_from_config(cfg).patrol()))
