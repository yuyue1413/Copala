"""CLI commands for copala."""

import asyncio
import os
import select
import signal
import sys
from pathlib import Path

# Force UTF-8 encoding for Windows console
if sys.platform == "win32":
    if sys.stdout.encoding != "utf-8":
        os.environ["PYTHONIOENCODING"] = "utf-8"
        # Re-open stdout/stderr with UTF-8 encoding
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

import typer
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text

from copala import __logo__, __version__
from copala.config.paths import get_workspace_path
from copala.config.schema import Config
from copala.utils.helpers import sync_workspace_templates

app = typer.Typer(
    name="copala",
    help=f"{__logo__} copala - Personal AI Assistant",
    no_args_is_help=True,
)

console = Console()
EXIT_COMMANDS = {"exit", "quit", "/exit", "/quit", ":q"}

# ---------------------------------------------------------------------------
# CLI input: prompt_toolkit for editing, paste, history, and display
# ---------------------------------------------------------------------------

_PROMPT_SESSION: PromptSession | None = None
_SAVED_TERM_ATTRS = None  # original termios settings, restored on exit


def _flush_pending_tty_input() -> None:
    """Drop unread keypresses typed while the model was generating output."""
    try:
        fd = sys.stdin.fileno()
        if not os.isatty(fd):
            return
    except Exception:
        return

    try:
        import termios
        termios.tcflush(fd, termios.TCIFLUSH)
        return
    except Exception:
        pass

    try:
        while True:
            ready, _, _ = select.select([fd], [], [], 0)
            if not ready:
                break
            if not os.read(fd, 4096):
                break
    except Exception:
        return


def _restore_terminal() -> None:
    """Restore terminal to its original state (echo, line buffering, etc.)."""
    if _SAVED_TERM_ATTRS is None:
        return
    try:
        import termios
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, _SAVED_TERM_ATTRS)
    except Exception:
        pass


def _init_prompt_session() -> None:
    """Create the prompt_toolkit session with persistent file history."""
    global _PROMPT_SESSION, _SAVED_TERM_ATTRS

    # Save terminal state so we can restore it on exit
    try:
        import termios
        _SAVED_TERM_ATTRS = termios.tcgetattr(sys.stdin.fileno())
    except Exception:
        pass

    from copala.config.paths import get_cli_history_path

    history_file = get_cli_history_path()
    history_file.parent.mkdir(parents=True, exist_ok=True)

    _PROMPT_SESSION = PromptSession(
        history=FileHistory(str(history_file)),
        enable_open_in_editor=False,
        multiline=False,   # Enter submits (single line mode)
    )


def _print_agent_response(response: str, render_markdown: bool) -> None:
    """Render assistant response with consistent terminal styling."""
    content = response or ""
    body = Markdown(content) if render_markdown else Text(content)
    console.print()
    console.print(f"[cyan]{__logo__} copala[/cyan]")
    console.print(body)
    console.print()


def _is_exit_command(command: str) -> bool:
    """Return True when input should end interactive chat."""
    return command.lower() in EXIT_COMMANDS


async def _read_interactive_input_async() -> str:
    """Read user input using prompt_toolkit (handles paste, history, display).

    prompt_toolkit natively handles:
    - Multiline paste (bracketed paste mode)
    - History navigation (up/down arrows)
    - Clean display (no ghost characters or artifacts)
    """
    if _PROMPT_SESSION is None:
        raise RuntimeError("Call _init_prompt_session() first")
    try:
        with patch_stdout():
            return await _PROMPT_SESSION.prompt_async(
                HTML("<b fg='ansiblue'>You:</b> "),
            )
    except EOFError as exc:
        raise KeyboardInterrupt from exc



def version_callback(value: bool):
    if value:
        console.print(f"{__logo__} copala v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        None, "--version", "-v", callback=version_callback, is_eager=True
    ),
):
    """copala - Personal AI Assistant."""
    pass


# ============================================================================
# Onboard / Setup
# ============================================================================


@app.command()
def onboard():
    """Initialize copala configuration and workspace."""
    from copala.config.loader import get_config_path, load_config, save_config
    from copala.config.schema import Config

    config_path = get_config_path()

    if config_path.exists():
        console.print(f"[yellow]Config already exists at {config_path}[/yellow]")
        console.print("  [bold]y[/bold] = overwrite with defaults (existing values will be lost)")
        console.print("  [bold]N[/bold] = refresh config, keeping existing values and adding new fields")
        if typer.confirm("Overwrite?"):
            config = Config()
            save_config(config)
            console.print(f"[green]✓[/green] Config reset to defaults at {config_path}")
        else:
            config = load_config()
            save_config(config)
            console.print(f"[green]✓[/green] Config refreshed at {config_path} (existing values preserved)")
    else:
        save_config(Config())
        console.print(f"[green]✓[/green] Created config at {config_path}")

    # Create workspace
    workspace = get_workspace_path()

    if not workspace.exists():
        workspace.mkdir(parents=True, exist_ok=True)
        console.print(f"[green]✓[/green] Created workspace at {workspace}")

    sync_workspace_templates(workspace)

    console.print(f"\n{__logo__} copala is ready!")
    console.print("\nNext steps:")
    console.print("  1. Add your API key to [cyan]~/.copala/config.json[/cyan]")
    console.print("     Get one at: https://openrouter.ai/keys")
    console.print("  2. Chat: [cyan]copala agent -m \"Hello!\"[/cyan]")
    console.print("\n[dim]Want Telegram/WhatsApp? See: https://github.com/HKUDS/copala#-chat-apps[/dim]")





def _make_provider(config: Config):
    """Create the appropriate LLM provider from config."""
    from copala.config.paths import get_runtime_subdir
    from copala.providers.fallback_provider import FallbackLLMProvider

    primary = _make_single_provider(
        config,
        model=config.agents.defaults.model,
        provider_override=config.agents.defaults.provider,
    )
    fallback_cfg = config.agents.fallback
    if not fallback_cfg.enabled or not fallback_cfg.model:
        return primary

    def _fallback_factory():
        return _make_single_provider(
            config,
            model=fallback_cfg.model,
            provider_override=fallback_cfg.provider,
        )

    return FallbackLLMProvider(
        primary=primary,
        fallback_factory=_fallback_factory,
        primary_model=config.agents.defaults.model,
        fallback_model=fallback_cfg.model,
        state_path=get_runtime_subdir("llm") / "fallback_state.json",
    )


def _make_single_provider(config: Config, *, model: str, provider_override: str = "auto"):
    """Create one concrete LLM provider for a specific model."""
    from copala.providers.openai_codex_provider import OpenAICodexProvider
    from copala.providers.azure_openai_provider import AzureOpenAIProvider

    provider_name = config.get_provider_name(model) if provider_override == "auto" else provider_override
    p = config.get_provider(model)
    if provider_override != "auto":
        p = getattr(config.providers, provider_override, p)
    api_base = p.api_base if p and p.api_base else config.get_api_base(model)

    # OpenAI Codex (OAuth)
    if provider_name == "openai_codex" or model.startswith("openai-codex/"):
        return OpenAICodexProvider(default_model=model)

    # Custom: direct OpenAI-compatible endpoint, bypasses LiteLLM
    from copala.providers.custom_provider import CustomProvider
    if provider_name == "custom":
        return CustomProvider(
            api_key=p.api_key if p else "no-key",
            api_base=api_base or "http://localhost:8000/v1",
            default_model=model,
        )

    # Azure OpenAI: direct Azure OpenAI endpoint with deployment name
    if provider_name == "azure_openai":
        if not p or not p.api_key or not p.api_base:
            console.print("[red]Error: Azure OpenAI requires api_key and api_base.[/red]")
            console.print("Set them in ~/.copala/config.json under providers.azure_openai section")
            console.print("Use the model field to specify the deployment name.")
            raise typer.Exit(1)
        
        return AzureOpenAIProvider(
            api_key=p.api_key,
            api_base=p.api_base,
            default_model=model,
        )

    from copala.providers.litellm_provider import LiteLLMProvider
    from copala.providers.registry import find_by_name
    spec = find_by_name(provider_name)
    if not model.startswith("bedrock/") and not (p and p.api_key) and not (spec and spec.is_oauth):
        console.print("[red]Error: No API key configured.[/red]")
        console.print("Set one in ~/.copala/config.json under providers section")
        raise typer.Exit(1)

    return LiteLLMProvider(
        api_key=p.api_key if p else None,
        api_base=api_base,
        default_model=model,
        extra_headers=p.extra_headers if p else None,
        provider_name=provider_name,
    )


def _load_runtime_config(config: str | None = None, workspace: str | None = None) -> Config:
    """Load config and optionally override the active workspace."""
    from copala.config.loader import load_config, set_config_path

    config_path = None
    if config:
        config_path = Path(config).expanduser().resolve()
        if not config_path.exists():
            console.print(f"[red]Error: Config file not found: {config_path}[/red]")
            raise typer.Exit(1)
        set_config_path(config_path)
        console.print(f"[dim]Using config: {config_path}[/dim]")

    loaded = load_config(config_path)
    if workspace:
        loaded.agents.defaults.workspace = workspace
    return loaded


# ============================================================================
# Gateway / Server
# ============================================================================


@app.command()
def gateway(
    port: int = typer.Option(18790, "--port", "-p", help="Gateway port"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Start the copala gateway."""
    from copala.agent.loop import AgentLoop
    from copala.bus.queue import MessageBus
    from copala.channels.manager import ChannelManager
    from copala.config.paths import get_cron_dir
    from copala.cron.service import CronService
    from copala.cron.types import CronJob
    from copala.heartbeat.service import HeartbeatService
    from copala.session.manager import SessionManager

    if verbose:
        import logging
        logging.basicConfig(level=logging.DEBUG)

    config = _load_runtime_config(config, workspace)

    console.print(f"{__logo__} Starting copala gateway on port {port}...")
    sync_workspace_templates(config.workspace_path)
    bus = MessageBus()
    provider = _make_provider(config)
    session_manager = SessionManager(config.workspace_path)

    # Create cron service first (callback set after agent creation)
    cron_store_path = get_cron_dir() / "jobs.json"
    cron = CronService(cron_store_path)

    # Create agent with cron service
    agent = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        model=config.agents.defaults.model,
        temperature=config.agents.defaults.temperature,
        max_tokens=config.agents.defaults.max_tokens,
        max_iterations=config.agents.defaults.max_tool_iterations,
        memory_window=config.agents.defaults.memory_window,
        reasoning_effort=config.agents.defaults.reasoning_effort,
        brave_api_key=config.tools.web.search.api_key or None,
        web_search_config=config.tools.web.search,
        web_proxy=config.tools.web.proxy or None,
        exec_config=config.tools.exec,
        cron_service=cron,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        session_manager=session_manager,
        mcp_servers=config.tools.mcp_servers,
        channels_config=config.channels,
        mailbox_config=config.mailbox,
        memory_rag_config=config.memory_rag,
        calendar_config=config.calendar,
        life_events_config=config.life_events,
        daily_planning_config=config.daily_planning,
        outing_planning_config=config.outing_planning,
        smart_reminders_config=config.smart_reminders,
    )

    # Create channel manager
    channels = ChannelManager(config, bus)

    def _pick_active_target() -> tuple[str, str]:
        """Pick a routable channel/chat target for background-triggered messages."""
        enabled = set(channels.enabled_channels)
        # Prefer the most recently updated non-internal session on an enabled channel.
        for item in session_manager.list_sessions():
            key = item.get("key") or ""
            if ":" not in key:
                continue
            channel, chat_id = key.split(":", 1)
            if channel in {"cli", "system"}:
                continue
            if channel in enabled and chat_id:
                return channel, chat_id
        # Fallback keeps prior behavior but remains explicit.
        return "cli", "direct"

    # Set cron callback (needs agent)
    async def on_cron_job(job: CronJob) -> str | None:
        """Execute a cron job through the agent."""
        from copala.agent.tools.cron import CronTool
        from copala.agent.tools.message import MessageTool

        if agent.life_events and job.name == "Life Events Email Patrol (Auto)":
            return await agent.life_events.patrol_email(limit=config.mailbox.fetch_limit)
        if agent.life_events and job.name == "Life Events Calendar Patrol (Auto)":
            return await agent.life_events.patrol_calendar()
        if getattr(agent, "smart_reminders", None) and job.name == "Smart Reminders Patrol (Auto)":
            return await agent.smart_reminders.patrol()

        reminder_note = (
            "[Scheduled Task] Timer finished.\n\n"
            f"Task '{job.name}' has been triggered.\n"
            f"Scheduled instruction: {job.payload.message}"
        )

        channel = job.payload.channel
        chat_id = job.payload.to
        if not channel or not chat_id:
            channel, chat_id = _pick_active_target()

        # Prevent the agent from scheduling new cron jobs during execution
        cron_tool = agent.tools.get("cron")
        cron_token = None
        if isinstance(cron_tool, CronTool):
            cron_token = cron_tool.set_cron_context(True)
        try:
            response = await agent.process_direct(
                reminder_note,
                session_key=f"cron:{job.id}",
                channel=channel,
                chat_id=chat_id,
            )
        finally:
            if isinstance(cron_tool, CronTool) and cron_token is not None:
                cron_tool.reset_cron_context(cron_token)

        message_tool = agent.tools.get("message")
        if isinstance(message_tool, MessageTool) and message_tool._sent_in_turn:
            return response

        if job.payload.deliver and response:
            if response.strip() == "IGNORE":
                return response

            from copala.bus.events import OutboundMessage
            await bus.publish_outbound(OutboundMessage(
                channel=channel,
                chat_id=chat_id,
                content=response
            ))
        return response
    cron.on_job = on_cron_job

    # Create heartbeat service
    async def on_heartbeat_execute(tasks: str) -> str:
        """Phase 2: execute heartbeat tasks through the full agent loop."""
        channel, chat_id = _pick_active_target()

        async def _silent(*_args, **_kwargs):
            pass

        return await agent.process_direct(
            tasks,
            session_key="heartbeat",
            channel=channel,
            chat_id=chat_id,
            on_progress=_silent,
        )

    async def on_heartbeat_notify(response: str) -> None:
        """Deliver a heartbeat response to the user's channel."""
        from copala.bus.events import OutboundMessage
        channel, chat_id = _pick_active_target()
        if channel == "cli":
            return  # No external channel available to deliver to
        await bus.publish_outbound(OutboundMessage(channel=channel, chat_id=chat_id, content=response))

    hb_cfg = config.gateway.heartbeat
    heartbeat = HeartbeatService(
        workspace=config.workspace_path,
        provider=provider,
        model=agent.model,
        on_execute=on_heartbeat_execute,
        on_notify=on_heartbeat_notify,
        interval_s=hb_cfg.interval_s,
        enabled=hb_cfg.enabled,
    )

    if channels.enabled_channels:
        console.print(f"[green]✓[/green] Channels enabled: {', '.join(channels.enabled_channels)}")
    else:
        console.print("[yellow]Warning: No channels enabled[/yellow]")

    # Auto-register email patrol job if enabled
    if config.mailbox.enabled and config.mailbox.username and not config.life_events.enabled:
        existing_jobs = cron.list_jobs(include_disabled=True)
        job_name = "Email Patrol (Auto)"
        if not any(j.name == job_name for j in existing_jobs):
            from copala.cron.types import CronSchedule
            cron.add_job(
                name=job_name,
                schedule=CronSchedule(kind="every", every_ms=config.mailbox.check_interval_minutes * 60 * 1000),
                message=f"Check for new emails (limit: {config.mailbox.fetch_limit}). Follow the 'Email Processing' skill to compress and summarize important ones. If no important emails are found, do not generate a report.",
                deliver=True,
            )
            console.print(f"[green]✓[/green] Mailbox: Auto-patrol registered (every {config.mailbox.check_interval_minutes}m)")
        else:
            console.print(f"[green]✓[/green] Mailbox: Auto-patrol active")

    if config.life_events.enabled:
        existing_jobs = cron.list_jobs(include_disabled=True)
        from copala.cron.types import CronSchedule

        email_job_name = "Life Events Email Patrol (Auto)"
        if config.mailbox.enabled and config.mailbox.username and not any(j.name == email_job_name for j in existing_jobs):
            cron.add_job(
                name=email_job_name,
                schedule=CronSchedule(kind="every", every_ms=max(1, config.life_events.email_patrol_minutes) * 60 * 1000),
                message="Life events email patrol",
                deliver=False,
            )
            console.print(f"[green]✓[/green] Life events: email patrol registered")

        calendar_job_name = "Life Events Calendar Patrol (Auto)"
        if config.calendar.enterprise_enabled and not any(j.name == calendar_job_name for j in existing_jobs):
            cron.add_job(
                name=calendar_job_name,
                schedule=CronSchedule(kind="every", every_ms=max(1, config.life_events.calendar_patrol_minutes) * 60 * 1000),
                message="Life events calendar patrol",
                deliver=False,
            )
            console.print(f"[green]✓[/green] Life events: calendar patrol registered")

    if config.smart_reminders.enabled:
        existing_jobs = cron.list_jobs(include_disabled=True)
        from copala.cron.types import CronSchedule

        smart_job_name = "Smart Reminders Patrol (Auto)"
        if not any(j.name == smart_job_name for j in existing_jobs):
            cron.add_job(
                name=smart_job_name,
                schedule=CronSchedule(kind="every", every_ms=max(1, config.smart_reminders.risk_patrol_minutes) * 60 * 1000),
                message="Smart reminders patrol",
                deliver=False,
            )
            console.print("[green]Smart reminders: patrol registered[/green]")

    cron_status = cron.status()
    if cron_status["jobs"] > 0:
        console.print(f"[green]✓[/green] Cron: {cron_status['jobs']} scheduled jobs")

    console.print(f"[green]✓[/green] Heartbeat: every {hb_cfg.interval_s}s")

    async def run():
        try:
            await cron.start()
            await heartbeat.start()
            await asyncio.gather(
                agent.run(),
                channels.start_all(),
            )
        except KeyboardInterrupt:
            console.print("\nShutting down...")
        finally:
            await agent.close_mcp()
            heartbeat.stop()
            cron.stop()
            agent.stop()
            await channels.stop_all()

    asyncio.run(run())




# ============================================================================
# Agent Commands
# ============================================================================


@app.command()
def agent(
    message: str = typer.Option(None, "--message", "-m", help="Message to send to the agent"),
    session_id: str = typer.Option("cli:direct", "--session", "-s", help="Session ID"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
    markdown: bool = typer.Option(True, "--markdown/--no-markdown", help="Render assistant output as Markdown"),
    logs: bool = typer.Option(False, "--logs/--no-logs", help="Show copala runtime logs during chat"),
):
    """Interact with the agent directly."""
    from loguru import logger

    from copala.agent.loop import AgentLoop
    from copala.bus.queue import MessageBus
    from copala.config.paths import get_cron_dir
    from copala.cron.service import CronService

    config = _load_runtime_config(config, workspace)
    sync_workspace_templates(config.workspace_path)

    bus = MessageBus()
    provider = _make_provider(config)

    # Create cron service for tool usage (no callback needed for CLI unless running)
    cron_store_path = get_cron_dir() / "jobs.json"
    cron = CronService(cron_store_path)

    if logs:
        logger.enable("copala")
    else:
        logger.disable("copala")

    agent_loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        model=config.agents.defaults.model,
        temperature=config.agents.defaults.temperature,
        max_tokens=config.agents.defaults.max_tokens,
        max_iterations=config.agents.defaults.max_tool_iterations,
        memory_window=config.agents.defaults.memory_window,
        reasoning_effort=config.agents.defaults.reasoning_effort,
        brave_api_key=config.tools.web.search.api_key or None,
        web_search_config=config.tools.web.search,
        web_proxy=config.tools.web.proxy or None,
        exec_config=config.tools.exec,
        cron_service=cron,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        mcp_servers=config.tools.mcp_servers,
        channels_config=config.channels,
        mailbox_config=config.mailbox,
        memory_rag_config=config.memory_rag,
        calendar_config=config.calendar,
        life_events_config=config.life_events,
        daily_planning_config=config.daily_planning,
        outing_planning_config=config.outing_planning,
        smart_reminders_config=config.smart_reminders,
    )

    # Show spinner when logs are off (no output to miss); skip when logs are on
    def _thinking_ctx():
        if logs:
            from contextlib import nullcontext
            return nullcontext()
        # Animated spinner is safe to use with prompt_toolkit input handling
        return console.status("[dim]copala is thinking...[/dim]", spinner="dots")

    async def _cli_progress(content: str, *, tool_hint: bool = False) -> None:
        ch = agent_loop.channels_config
        if ch and tool_hint and not ch.send_tool_hints:
            return
        if ch and not tool_hint and not ch.send_progress:
            return
        console.print(f"  [dim]↳ {content}[/dim]")

    if message:
        # Single message mode — direct call, no bus needed
        async def run_once():
            with _thinking_ctx():
                response = await agent_loop.process_direct(message, session_id, on_progress=_cli_progress)
            _print_agent_response(response, render_markdown=markdown)
            await agent_loop.close_mcp()

        asyncio.run(run_once())
    else:
        # Interactive mode — route through bus like other channels
        from copala.bus.events import InboundMessage
        _init_prompt_session()
        console.print(f"{__logo__} Interactive mode (type [bold]exit[/bold] or [bold]Ctrl+C[/bold] to quit)\n")

        if ":" in session_id:
            cli_channel, cli_chat_id = session_id.split(":", 1)
        else:
            cli_channel, cli_chat_id = "cli", session_id

        def _handle_signal(signum, frame):
            sig_name = signal.Signals(signum).name
            _restore_terminal()
            console.print(f"\nReceived {sig_name}, goodbye!")
            sys.exit(0)

        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)
        # SIGHUP is not available on Windows
        if hasattr(signal, 'SIGHUP'):
            signal.signal(signal.SIGHUP, _handle_signal)
        # Ignore SIGPIPE to prevent silent process termination when writing to closed pipes
        # SIGPIPE is not available on Windows
        if hasattr(signal, 'SIGPIPE'):
            signal.signal(signal.SIGPIPE, signal.SIG_IGN)

        async def run_interactive():
            bus_task = asyncio.create_task(agent_loop.run())
            turn_done = asyncio.Event()
            turn_done.set()
            turn_response: list[str] = []

            async def _consume_outbound():
                while True:
                    try:
                        msg = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
                        if msg.metadata.get("_progress"):
                            is_tool_hint = msg.metadata.get("_tool_hint", False)
                            ch = agent_loop.channels_config
                            if ch and is_tool_hint and not ch.send_tool_hints:
                                pass
                            elif ch and not is_tool_hint and not ch.send_progress:
                                pass
                            else:
                                console.print(f"  [dim]↳ {msg.content}[/dim]")
                        elif not turn_done.is_set():
                            if msg.content:
                                turn_response.append(msg.content)
                            turn_done.set()
                        elif msg.content:
                            console.print()
                            _print_agent_response(msg.content, render_markdown=markdown)
                    except asyncio.TimeoutError:
                        continue
                    except asyncio.CancelledError:
                        break

            outbound_task = asyncio.create_task(_consume_outbound())

            try:
                while True:
                    try:
                        _flush_pending_tty_input()
                        user_input = await _read_interactive_input_async()
                        command = user_input.strip()
                        if not command:
                            continue

                        if _is_exit_command(command):
                            _restore_terminal()
                            console.print("\nGoodbye!")
                            break

                        turn_done.clear()
                        turn_response.clear()

                        await bus.publish_inbound(InboundMessage(
                            channel=cli_channel,
                            sender_id="user",
                            chat_id=cli_chat_id,
                            content=user_input,
                        ))

                        with _thinking_ctx():
                            await turn_done.wait()

                        if turn_response:
                            _print_agent_response(turn_response[0], render_markdown=markdown)
                    except KeyboardInterrupt:
                        _restore_terminal()
                        console.print("\nGoodbye!")
                        break
                    except EOFError:
                        _restore_terminal()
                        console.print("\nGoodbye!")
                        break
            finally:
                agent_loop.stop()
                outbound_task.cancel()
                await asyncio.gather(bus_task, outbound_task, return_exceptions=True)
                await agent_loop.close_mcp()

        asyncio.run(run_interactive())


# ============================================================================
# Channel Commands
# ============================================================================


memory_app = typer.Typer(help="Manage RAG-backed personal memory")
app.add_typer(memory_app, name="memory")


@memory_app.command("reindex")
def memory_reindex(
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
):
    """Reindex workspace memory files into the RAG store."""
    from copala.agent.memory_rag.store import MemoryRAGStore

    cfg = _load_runtime_config(config, workspace)
    sync_workspace_templates(cfg.workspace_path, silent=True)
    count = MemoryRAGStore(cfg.workspace_path).reindex_workspace()
    console.print(f"[green]Indexed {count} memory item(s).[/green]")


@memory_app.command("search")
def memory_search(
    query: str = typer.Argument(..., help="Memory search query"),
    limit: int = typer.Option(8, "--limit", "-n", help="Number of results"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
):
    """Search personal memory with BM25 + dense RRF retrieval."""
    from copala.agent.memory_rag.store import MemoryRAGStore

    cfg = _load_runtime_config(config, workspace)
    results = MemoryRAGStore(cfg.workspace_path).retrieve(query, top_k=limit)
    table = Table(title="Personal Memory Search")
    table.add_column("Type", style="cyan")
    table.add_column("Score", style="green")
    table.add_column("Source", style="yellow")
    table.add_column("Text")
    for result in results:
        item = result.item
        table.add_row(item.type, f"{result.score:.3f}", item.source, item.text[:180])
    console.print(table if results else "[yellow]No relevant memory found.[/yellow]")


@memory_app.command("list")
def memory_list(
    type: str | None = typer.Option(None, "--type", help="Filter by memory type"),
    limit: int = typer.Option(20, "--limit", "-n", help="Number of items"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
):
    """List stored personal memory items."""
    from copala.agent.memory_rag.store import MemoryRAGStore

    cfg = _load_runtime_config(config, workspace)
    items = MemoryRAGStore(cfg.workspace_path).list_items(mem_type=type, limit=limit)
    table = Table(title="Personal Memory")
    table.add_column("ID", style="dim")
    table.add_column("Type", style="cyan")
    table.add_column("Source", style="yellow")
    table.add_column("Text")
    for item in items:
        table.add_row(item.id[:10], item.type, item.source, item.text[:180])
    console.print(table if items else "[yellow]No memory items found.[/yellow]")


calendar_app = typer.Typer(help="Manage local and enterprise calendar ingestion")
app.add_typer(calendar_app, name="calendar")

life_events_app = typer.Typer(help="Manage personal life events")
app.add_typer(life_events_app, name="life-events")

smart_reminders_app = typer.Typer(help="Manage context-aware smart reminders")
app.add_typer(smart_reminders_app, name="smart-reminders")


def _calendar_service_from_config(config: Config):
    from copala.calendar.service import CalendarService

    cal = config.calendar
    return CalendarService(
        config.workspace_path,
        ics_files=cal.ics_files,
        ics_urls=cal.ics_urls,
        local_json_files=cal.local_json_files,
        lookahead_days=cal.lookahead_days,
        calendar_config=cal,
        feishu_config=config.channels.feishu,
    )


def _life_event_manager_from_config(config: Config):
    from copala.life_events.service import LifeEventManager

    return LifeEventManager(
        workspace=config.workspace_path,
        config=config.life_events,
        calendar_service=_calendar_service_from_config(config),
        memory_store=None,
        cron_service=None,
        send_callback=None,
        mailbox_config=config.mailbox,
    )


def _smart_reminder_service_from_config(config: Config, *, include_life_events: bool = True):
    from copala.smart_reminders.service import SmartReminderService

    return SmartReminderService(
        workspace=config.workspace_path,
        config=config.smart_reminders,
        calendar_service=_calendar_service_from_config(config),
        life_events=_life_event_manager_from_config(config) if include_life_events else None,
        daily_planning=None,
        memory_store=None,
        send_callback=None,
    )


@calendar_app.command("ingest")
def calendar_ingest(
    source: str = typer.Option("feishu", "--source", "-s", help="feishu, local, or all"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
):
    """Ingest configured calendar events into personal memory."""
    cfg = _load_runtime_config(config, workspace)
    count = asyncio.run(_calendar_service_from_config(cfg).ingest_async(source=source))
    console.print(f"[green]Ingested {count} calendar memory item(s) from {source}.[/green]")


@calendar_app.command("list")
def calendar_list(
    days: int = typer.Option(7, "--days", "-d", help="Days to look ahead"),
    source: str = typer.Option("feishu", "--source", "-s", help="feishu, local, or all"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
):
    """List upcoming calendar events."""
    from datetime import date, timedelta

    cfg = _load_runtime_config(config, workspace)
    events = asyncio.run(_calendar_service_from_config(cfg).list_events_async(
        start_date=date.today(),
        end_date=date.today() + timedelta(days=days),
        source=source,
    ))
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
    cfg = _load_runtime_config(config, workspace)
    calendars = asyncio.run(_calendar_service_from_config(cfg).list_calendars_async(platform=platform))
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
    cfg = _load_runtime_config(config, workspace)
    status = _calendar_service_from_config(cfg).auth_status()
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
    cfg = _load_runtime_config(config, workspace)
    url = _calendar_service_from_config(cfg).feishu_authorization_url(state=state)
    console.print("[green]Open this URL in your browser, approve it, then copy the returned code:[/green]")
    console.print(url)


@calendar_app.command("feishu-auth-code")
def calendar_feishu_auth_code(
    code: str = typer.Argument(..., help="Authorization code returned by Feishu OAuth"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
):
    """Exchange a Feishu OAuth code for a user token stored in the workspace."""
    cfg = _load_runtime_config(config, workspace)
    token = asyncio.run(_calendar_service_from_config(cfg).feishu_exchange_code(code))
    expires_at = token.get("expires_at")
    console.print("[green]Feishu user calendar authorization saved.[/green]")
    if expires_at:
        from datetime import datetime

        console.print(f"[dim]Expires at: {datetime.fromtimestamp(float(expires_at)).isoformat()}[/dim]")


@calendar_app.command("feishu-auth-login")
def calendar_feishu_auth_login(
    state: str = typer.Option("copala", "--state", help="OAuth state value"),
    timeout: int = typer.Option(300, "--timeout", help="Seconds to wait for the browser callback"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
):
    """Start a temporary local callback server and finish Feishu OAuth automatically."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Event
    from urllib.parse import parse_qs, urlparse

    cfg = _load_runtime_config(config, workspace)
    service = _calendar_service_from_config(cfg)
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
        console.print("[yellow]Stop any process using that port, or copy code from the failed browser URL and run:[/yellow]")
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


@life_events_app.command("list")
def life_events_list(
    limit: int = typer.Option(20, "--limit", "-n", help="Max events to show"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
):
    """List recorded personal life events."""
    cfg = _load_runtime_config(config, workspace)
    events = _life_event_manager_from_config(cfg).list_events(limit=limit)
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
    cfg = _load_runtime_config(config, workspace)
    result = asyncio.run(_life_event_manager_from_config(cfg).patrol_email(limit=limit))
    console.print(result)


@smart_reminders_app.command("status")
def smart_reminders_status(
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
):
    """Show smart reminder and Amap configuration status."""
    cfg = _load_runtime_config(config, workspace)
    console.print(_smart_reminder_service_from_config(cfg, include_life_events=False).status())


@smart_reminders_app.command("check")
def smart_reminders_check(
    days: int = typer.Option(2, "--days", "-d", help="Days to look ahead"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
):
    """Check upcoming weather, traffic and deadline risks."""
    cfg = _load_runtime_config(config, workspace)
    service = _smart_reminder_service_from_config(cfg)
    risks = asyncio.run(service.check_risks(days=days))
    console.print(service.format_risks(risks))


@smart_reminders_app.command("patrol")
def smart_reminders_patrol(
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
):
    """Run one smart-reminder patrol pass."""
    cfg = _load_runtime_config(config, workspace)
    console.print(asyncio.run(_smart_reminder_service_from_config(cfg).patrol()))


channels_app = typer.Typer(help="Manage channels")
app.add_typer(channels_app, name="channels")


@channels_app.command("status")
def channels_status():
    """Show channel status."""
    from copala.config.loader import load_config

    config = load_config()

    table = Table(title="Channel Status")
    table.add_column("Channel", style="cyan")
    table.add_column("Enabled", style="green")
    table.add_column("Configuration", style="yellow")

    # WhatsApp
    wa = config.channels.whatsapp
    table.add_row(
        "WhatsApp",
        "✓" if wa.enabled else "✗",
        wa.bridge_url
    )

    dc = config.channels.discord
    table.add_row(
        "Discord",
        "✓" if dc.enabled else "✗",
        dc.gateway_url
    )

    # Feishu
    fs = config.channels.feishu
    fs_config = f"app_id: {fs.app_id[:10]}..." if fs.app_id else "[dim]not configured[/dim]"
    table.add_row(
        "Feishu",
        "✓" if fs.enabled else "✗",
        fs_config
    )

    # Mochat
    mc = config.channels.mochat
    mc_base = mc.base_url or "[dim]not configured[/dim]"
    table.add_row(
        "Mochat",
        "✓" if mc.enabled else "✗",
        mc_base
    )

    # Telegram
    tg = config.channels.telegram
    tg_config = f"token: {tg.token[:10]}..." if tg.token else "[dim]not configured[/dim]"
    table.add_row(
        "Telegram",
        "✓" if tg.enabled else "✗",
        tg_config
    )

    # Slack
    slack = config.channels.slack
    slack_config = "socket" if slack.app_token and slack.bot_token else "[dim]not configured[/dim]"
    table.add_row(
        "Slack",
        "✓" if slack.enabled else "✗",
        slack_config
    )

    # DingTalk
    dt = config.channels.dingtalk
    dt_config = f"client_id: {dt.client_id[:10]}..." if dt.client_id else "[dim]not configured[/dim]"
    table.add_row(
        "DingTalk",
        "✓" if dt.enabled else "✗",
        dt_config
    )

    # QQ
    qq = config.channels.qq
    qq_config = f"app_id: {qq.app_id[:10]}..." if qq.app_id else "[dim]not configured[/dim]"
    table.add_row(
        "QQ",
        "✓" if qq.enabled else "✗",
        qq_config
    )

    # Email
    em = config.channels.email
    em_config = em.imap_host if em.imap_host else "[dim]not configured[/dim]"
    table.add_row(
        "Email",
        "✓" if em.enabled else "✗",
        em_config
    )

    console.print(table)


def _get_bridge_dir() -> Path:
    """Get the bridge directory, setting it up if needed."""
    import shutil
    import subprocess

    # User's bridge location
    from copala.config.paths import get_bridge_install_dir

    user_bridge = get_bridge_install_dir()

    # Check if already built
    if (user_bridge / "dist" / "index.js").exists():
        return user_bridge

    # Check for npm
    if not shutil.which("npm"):
        console.print("[red]npm not found. Please install Node.js >= 18.[/red]")
        raise typer.Exit(1)

    # Find source bridge: first check package data, then source dir
    pkg_bridge = Path(__file__).parent.parent / "bridge"  # copala/bridge (installed)
    src_bridge = Path(__file__).parent.parent.parent / "bridge"  # repo root/bridge (dev)

    source = None
    if (pkg_bridge / "package.json").exists():
        source = pkg_bridge
    elif (src_bridge / "package.json").exists():
        source = src_bridge

    if not source:
        console.print("[red]Bridge source not found.[/red]")
        console.print("Try reinstalling: pip install --force-reinstall copala")
        raise typer.Exit(1)

    console.print(f"{__logo__} Setting up bridge...")

    # Copy to user directory
    user_bridge.parent.mkdir(parents=True, exist_ok=True)
    if user_bridge.exists():
        shutil.rmtree(user_bridge)
    shutil.copytree(source, user_bridge, ignore=shutil.ignore_patterns("node_modules", "dist"))

    # Install and build
    try:
        console.print("  Installing dependencies...")
        subprocess.run(["npm", "install"], cwd=user_bridge, check=True, capture_output=True)

        console.print("  Building...")
        subprocess.run(["npm", "run", "build"], cwd=user_bridge, check=True, capture_output=True)

        console.print("[green]✓[/green] Bridge ready\n")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Build failed: {e}[/red]")
        if e.stderr:
            console.print(f"[dim]{e.stderr.decode()[:500]}[/dim]")
        raise typer.Exit(1)

    return user_bridge


@channels_app.command("login")
def channels_login():
    """Link device via QR code."""
    import subprocess

    from copala.config.loader import load_config
    from copala.config.paths import get_runtime_subdir

    config = load_config()
    bridge_dir = _get_bridge_dir()

    console.print(f"{__logo__} Starting bridge...")
    console.print("Scan the QR code to connect.\n")

    env = {**os.environ}
    if config.channels.whatsapp.bridge_token:
        env["BRIDGE_TOKEN"] = config.channels.whatsapp.bridge_token
    env["AUTH_DIR"] = str(get_runtime_subdir("whatsapp-auth"))

    try:
        subprocess.run(["npm", "start"], cwd=bridge_dir, check=True, env=env)
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Bridge failed: {e}[/red]")
    except FileNotFoundError:
        console.print("[red]npm not found. Please install Node.js.[/red]")


# ============================================================================
# Status Commands
# ============================================================================


@app.command()
def status():
    """Show copala status."""
    from copala.config.loader import get_config_path, load_config

    config_path = get_config_path()
    config = load_config()
    workspace = config.workspace_path

    console.print(f"{__logo__} copala Status\n")

    console.print(f"Config: {config_path} {'[green]✓[/green]' if config_path.exists() else '[red]✗[/red]'}")
    console.print(f"Workspace: {workspace} {'[green]✓[/green]' if workspace.exists() else '[red]✗[/red]'}")

    if config_path.exists():
        from copala.providers.registry import PROVIDERS

        console.print(f"Model: {config.agents.defaults.model}")

        # Check API keys from registry
        for spec in PROVIDERS:
            p = getattr(config.providers, spec.name, None)
            if p is None:
                continue
            if spec.is_oauth:
                console.print(f"{spec.label}: [green]✓ (OAuth)[/green]")
            elif spec.is_local:
                # Local deployments show api_base instead of api_key
                if p.api_base:
                    console.print(f"{spec.label}: [green]✓ {p.api_base}[/green]")
                else:
                    console.print(f"{spec.label}: [dim]not set[/dim]")
            else:
                has_key = bool(p.api_key)
                console.print(f"{spec.label}: {'[green]✓[/green]' if has_key else '[dim]not set[/dim]'}")


# ============================================================================
# OAuth Login
# ============================================================================

provider_app = typer.Typer(help="Manage providers")
app.add_typer(provider_app, name="provider")


_LOGIN_HANDLERS: dict[str, callable] = {}


def _register_login(name: str):
    def decorator(fn):
        _LOGIN_HANDLERS[name] = fn
        return fn
    return decorator


@provider_app.command("login")
def provider_login(
    provider: str = typer.Argument(..., help="OAuth provider (e.g. 'openai-codex', 'github-copilot')"),
):
    """Authenticate with an OAuth provider."""
    from copala.providers.registry import PROVIDERS

    key = provider.replace("-", "_")
    spec = next((s for s in PROVIDERS if s.name == key and s.is_oauth), None)
    if not spec:
        names = ", ".join(s.name.replace("_", "-") for s in PROVIDERS if s.is_oauth)
        console.print(f"[red]Unknown OAuth provider: {provider}[/red]  Supported: {names}")
        raise typer.Exit(1)

    handler = _LOGIN_HANDLERS.get(spec.name)
    if not handler:
        console.print(f"[red]Login not implemented for {spec.label}[/red]")
        raise typer.Exit(1)

    console.print(f"{__logo__} OAuth Login - {spec.label}\n")
    handler()


@_register_login("openai_codex")
def _login_openai_codex() -> None:
    try:
        from oauth_cli_kit import get_token, login_oauth_interactive
        token = None
        try:
            token = get_token()
        except Exception:
            pass
        if not (token and token.access):
            console.print("[cyan]Starting interactive OAuth login...[/cyan]\n")
            token = login_oauth_interactive(
                print_fn=lambda s: console.print(s),
                prompt_fn=lambda s: typer.prompt(s),
            )
        if not (token and token.access):
            console.print("[red]✗ Authentication failed[/red]")
            raise typer.Exit(1)
        console.print(f"[green]✓ Authenticated with OpenAI Codex[/green]  [dim]{token.account_id}[/dim]")
    except ImportError:
        console.print("[red]oauth_cli_kit not installed. Run: pip install oauth-cli-kit[/red]")
        raise typer.Exit(1)


@_register_login("github_copilot")
def _login_github_copilot() -> None:
    import asyncio

    console.print("[cyan]Starting GitHub Copilot device flow...[/cyan]\n")

    async def _trigger():
        from litellm import acompletion
        await acompletion(model="github_copilot/gpt-4o", messages=[{"role": "user", "content": "hi"}], max_tokens=1)

    try:
        asyncio.run(_trigger())
        console.print("[green]✓ Authenticated with GitHub Copilot[/green]")
    except Exception as e:
        console.print(f"[red]Authentication error: {e}[/red]")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
