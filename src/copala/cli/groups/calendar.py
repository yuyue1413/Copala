"""Calendar ingestion, diagnostics, and Feishu OAuth commands."""

import asyncio
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event
from urllib.parse import parse_qs, urlparse

import typer
from rich.console import Console
from rich.table import Table

from copala.cli.runtime import calendar_service_from_config, load_runtime_config

console = Console()
calendar_app = typer.Typer(help="Manage local and enterprise calendar ingestion")


@calendar_app.command("ingest")
def calendar_ingest(
    source: str = typer.Option("feishu", "--source", "-s", help="feishu, local, or all"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
):
    """Ingest configured calendar events into personal memory."""
    cfg = load_runtime_config(config, workspace)
    count = asyncio.run(calendar_service_from_config(cfg).ingest_async(source=source))
    console.print(f"[green]Ingested {count} calendar memory item(s) from {source}.[/green]")


@calendar_app.command("list")
def calendar_list(
    days: int = typer.Option(7, "--days", "-d", help="Days to look ahead"),
    source: str = typer.Option("feishu", "--source", "-s", help="feishu, local, or all"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
):
    """List upcoming calendar events."""
    cfg = load_runtime_config(config, workspace)
    events = asyncio.run(
        calendar_service_from_config(cfg).list_events_async(
            start_date=date.today(),
            end_date=date.today() + timedelta(days=days),
            source=source,
        )
    )
    table = Table(title="Calendar Events")
    table.add_column("Source", style="green")
    table.add_column("Start", style="cyan")
    table.add_column("Title")
    table.add_column("Location", style="yellow")
    for event in events:
        table.add_row(event.platform or "local", event.start.isoformat(), event.title, event.location)
    console.print(table if events else "[yellow]No calendar events found.[/yellow]")


@calendar_app.command("calendars")
def calendar_calendars(
    platform: str = typer.Option("feishu", "--platform", "-p", help="feishu"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
):
    """List enterprise calendars visible to the configured app."""
    cfg = load_runtime_config(config, workspace)
    calendars = asyncio.run(calendar_service_from_config(cfg).list_calendars_async(platform=platform))
    table = Table(title=f"{platform} Calendars")
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Primary", style="green")
    for calendar in calendars:
        table.add_row(calendar.id, calendar.name, "yes" if calendar.is_primary else "")
    console.print(table if calendars else "[yellow]No enterprise calendars found.[/yellow]")


@calendar_app.command("status")
def calendar_status(
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
):
    """Show non-secret calendar authentication diagnostics."""
    cfg = load_runtime_config(config, workspace)
    status = calendar_service_from_config(cfg).auth_status()
    table = Table(title="Calendar Auth Status")
    table.add_column("Provider", style="cyan")
    table.add_column("Key")
    table.add_column("Value", style="green")
    table.add_row("-", "workspace", str(status.get("workspace")))
    table.add_row("-", "enterprise_enabled", str(status.get("enterprise_enabled")))
    table.add_row("-", "default_platform", str(status.get("default_platform")))
    for provider, item in (status.get("providers") or {}).items():
        for key, value in item.items():
            table.add_row(provider, key, str(value))
    console.print(table)


@calendar_app.command("feishu-auth-url")
def calendar_feishu_auth_url(
    state: str = typer.Option("copala", "--state", help="OAuth state value"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
):
    """Print the Feishu OAuth URL for authorizing the current user's private calendar."""
    cfg = load_runtime_config(config, workspace)
    url = calendar_service_from_config(cfg).feishu_authorization_url(state=state)
    console.print("[green]Open this URL in your browser, approve it, then copy the returned code:[/green]")
    console.print(url)


@calendar_app.command("feishu-auth-code")
def calendar_feishu_auth_code(
    code: str = typer.Argument(..., help="Authorization code returned by Feishu OAuth"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
):
    """Exchange a Feishu OAuth code for a user token stored in the workspace."""
    cfg = load_runtime_config(config, workspace)
    token = asyncio.run(calendar_service_from_config(cfg).feishu_exchange_code(code))
    expires_at = token.get("expires_at")
    console.print("[green]Feishu user calendar authorization saved.[/green]")
    if expires_at:
        console.print(f"[dim]Expires at: {datetime.fromtimestamp(float(expires_at)).isoformat()}[/dim]")


@calendar_app.command("feishu-auth-login")
def calendar_feishu_auth_login(
    state: str = typer.Option("copala", "--state", help="OAuth state value"),
    timeout: int = typer.Option(300, "--timeout", help="Seconds to wait for the browser callback"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
):
    """Start a temporary local callback server and finish Feishu OAuth automatically."""
    cfg = load_runtime_config(config, workspace)
    service = calendar_service_from_config(cfg)
    redirect_uri = cfg.calendar.providers.feishu.redirect_uri
    parsed_redirect = urlparse(redirect_uri)
    host = parsed_redirect.hostname or "localhost"
    port = parsed_redirect.port or (443 if parsed_redirect.scheme == "https" else 80)
    callback_path = parsed_redirect.path or "/"
    done = Event()
    result: dict[str, str] = {}

    class FeishuOAuthHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path != callback_path:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Not found")
                return
            params = parse_qs(parsed.query)
            if params.get("state", [""])[0] != state:
                result["error"] = "OAuth state mismatch."
            elif params.get("error"):
                result["error"] = params.get("error_description", params["error"])[0]
            elif params.get("code"):
                code = params["code"][0]
                try:
                    token = asyncio.run(service.feishu_exchange_code(code))
                    result["ok"] = "1"
                    if token.get("expires_at"):
                        result["expires_at"] = str(token["expires_at"])
                except Exception as exc:
                    result["error"] = str(exc)
            else:
                result["error"] = "Feishu callback did not include code."

            ok = "ok" in result
            self.send_response(200 if ok else 500)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            if ok:
                body = "<h2>Feishu calendar authorization saved.</h2><p>You can close this page.</p>"
            else:
                body = f"<h2>Feishu calendar authorization failed.</h2><pre>{result.get('error')}</pre>"
            self.wfile.write(body.encode("utf-8"))
            done.set()

    try:
        server = ThreadingHTTPServer((host, port), FeishuOAuthHandler)
        server.timeout = 0.2
    except OSError as exc:
        console.print(f"[red]Cannot listen on {host}:{port}: {exc}[/red]")
        console.print(
            "[yellow]Stop any process using that port, or copy code from the failed browser "
            "URL and run:[/yellow]"
        )
        console.print("copala calendar feishu-auth-code YOUR_CODE")
        raise typer.Exit(1) from exc

    url = service.feishu_authorization_url(state=state)
    console.print("[green]Temporary Feishu OAuth callback server started.[/green]")
    console.print(f"[dim]Listening on {redirect_uri}[/dim]")
    console.print("[green]Open this URL in your browser and approve access:[/green]")
    console.print(url)

    try:
        remaining = float(timeout)
        while not done.is_set():
            server.handle_request()
            remaining -= 0.2
            if remaining <= 0:
                console.print("[red]Timed out waiting for Feishu OAuth callback.[/red]")
                raise typer.Exit(1)
    finally:
        server.server_close()

    if result.get("error"):
        console.print(f"[red]{result['error']}[/red]")
        raise typer.Exit(1)
    console.print("[green]Feishu user calendar authorization saved.[/green]")
