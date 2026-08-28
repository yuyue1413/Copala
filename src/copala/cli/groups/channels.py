"""Messaging channel status and bridge commands."""

import os
import shutil
import subprocess
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from copala import __logo__

console = Console()
channels_app = typer.Typer(help="Manage channels")


@channels_app.command("status")
def channels_status():
    """Show channel status."""
    from copala.config.loader import load_config

    config = load_config()
    table = Table(title="Channel Status")
    table.add_column("Channel", style="cyan")
    table.add_column("Enabled", style="green")
    table.add_column("Configuration", style="yellow")

    channel_rows = [
        ("Discord", config.channels.discord, config.channels.discord.gateway_url),
        (
            "Feishu",
            config.channels.feishu,
            f"app_id: {config.channels.feishu.app_id[:10]}..."
            if config.channels.feishu.app_id
            else "[dim]not configured[/dim]",
        ),
        ("Mochat", config.channels.mochat, config.channels.mochat.base_url or "[dim]not configured[/dim]"),
        (
            "Telegram",
            config.channels.telegram,
            f"token: {config.channels.telegram.token[:10]}..."
            if config.channels.telegram.token
            else "[dim]not configured[/dim]",
        ),
        (
            "Slack",
            config.channels.slack,
            "socket"
            if config.channels.slack.app_token and config.channels.slack.bot_token
            else "[dim]not configured[/dim]",
        ),
        (
            "DingTalk",
            config.channels.dingtalk,
            f"client_id: {config.channels.dingtalk.client_id[:10]}..."
            if config.channels.dingtalk.client_id
            else "[dim]not configured[/dim]",
        ),
        (
            "QQ",
            config.channels.qq,
            f"app_id: {config.channels.qq.app_id[:10]}..."
            if config.channels.qq.app_id
            else "[dim]not configured[/dim]",
        ),
        (
            "Email",
            config.channels.email,
            config.channels.email.imap_host or "[dim]not configured[/dim]",
        ),
    ]
    whatsapp = getattr(config.channels, "whatsapp", None)
    if whatsapp is not None:
        channel_rows.insert(0, ("WhatsApp", whatsapp, whatsapp.bridge_url))
    for name, channel, detail in channel_rows:
        table.add_row(name, "✓" if channel.enabled else "✗", detail)
    console.print(table)


def _get_bridge_dir() -> Path:
    """Get the WhatsApp bridge directory, setting it up if needed."""
    from copala.config.paths import get_bridge_install_dir

    user_bridge = get_bridge_install_dir()
    if (user_bridge / "dist" / "index.js").exists():
        return user_bridge
    if not shutil.which("npm"):
        console.print("[red]npm not found. Please install Node.js >= 18.[/red]")
        raise typer.Exit(1)

    pkg_bridge = Path(__file__).parents[2] / "bridge"
    repo_bridge = Path(__file__).parents[4] / "bridge"
    source = next((path for path in (pkg_bridge, repo_bridge) if (path / "package.json").exists()), None)
    if source is None:
        console.print("[red]Bridge source not found.[/red]")
        console.print("Try reinstalling: pip install --force-reinstall copala-ai")
        raise typer.Exit(1)

    console.print(f"{__logo__} Setting up bridge...")
    user_bridge.parent.mkdir(parents=True, exist_ok=True)
    if user_bridge.exists():
        shutil.rmtree(user_bridge)
    shutil.copytree(source, user_bridge, ignore=shutil.ignore_patterns("node_modules", "dist"))
    try:
        console.print("  Installing dependencies...")
        subprocess.run(["npm", "install"], cwd=user_bridge, check=True, capture_output=True)
        console.print("  Building...")
        subprocess.run(["npm", "run", "build"], cwd=user_bridge, check=True, capture_output=True)
        console.print("[green]✓[/green] Bridge ready\n")
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]Build failed: {exc}[/red]")
        if exc.stderr:
            console.print(f"[dim]{exc.stderr.decode()[:500]}[/dim]")
        raise typer.Exit(1) from exc
    return user_bridge


@channels_app.command("login")
def channels_login():
    """Link a WhatsApp device via QR code."""
    from copala.config.loader import load_config
    from copala.config.paths import get_runtime_subdir

    config = load_config()
    whatsapp = getattr(config.channels, "whatsapp", None)
    if whatsapp is None:
        console.print("[red]WhatsApp bridge is not available in this Copala build.[/red]")
        raise typer.Exit(1)
    bridge_dir = _get_bridge_dir()
    console.print(f"{__logo__} Starting bridge...")
    console.print("Scan the QR code to connect.\n")
    env = {**os.environ}
    if whatsapp.bridge_token:
        env["BRIDGE_TOKEN"] = whatsapp.bridge_token
    env["AUTH_DIR"] = str(get_runtime_subdir("whatsapp-auth"))
    try:
        subprocess.run(["npm", "start"], cwd=bridge_dir, check=True, env=env)
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]Bridge failed: {exc}[/red]")
    except FileNotFoundError:
        console.print("[red]npm not found. Please install Node.js.[/red]")
