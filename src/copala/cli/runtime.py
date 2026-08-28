"""Shared CLI runtime factories kept separate from command registration."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from copala.config.schema import Config

console = Console()


def make_provider(config: Config):
    """Create the primary provider and optional persisted fallback wrapper."""
    from copala.config.paths import get_runtime_subdir
    from copala.providers.fallback_provider import FallbackLLMProvider

    primary = make_single_provider(
        config,
        model=config.agents.defaults.model,
        provider_override=config.agents.defaults.provider,
    )
    fallback_cfg = config.agents.fallback
    if not fallback_cfg.enabled or not fallback_cfg.model:
        return primary

    def _fallback_factory():
        return make_single_provider(
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


def make_single_provider(config: Config, *, model: str, provider_override: str = "auto"):
    """Create one concrete LLM provider for a model."""
    from copala.providers.azure_openai_provider import AzureOpenAIProvider
    from copala.providers.custom_provider import CustomProvider
    from copala.providers.litellm_provider import LiteLLMProvider
    from copala.providers.openai_codex_provider import OpenAICodexProvider
    from copala.providers.registry import find_by_name

    provider_name = (
        config.get_provider_name(model) if provider_override == "auto" else provider_override
    )
    provider_config = config.get_provider(model)
    if provider_override != "auto":
        provider_config = getattr(config.providers, provider_override, provider_config)
    api_base = (
        provider_config.api_base
        if provider_config and provider_config.api_base
        else config.get_api_base(model)
    )

    if provider_name == "openai_codex" or model.startswith("openai-codex/"):
        return OpenAICodexProvider(default_model=model)
    if provider_name == "custom":
        return CustomProvider(
            api_key=provider_config.api_key if provider_config else "no-key",
            api_base=api_base or "http://localhost:8000/v1",
            default_model=model,
        )
    if provider_name == "azure_openai":
        if not provider_config or not provider_config.api_key or not provider_config.api_base:
            console.print("[red]Error: Azure OpenAI requires api_key and api_base.[/red]")
            console.print("Set them in ~/.copala/config.json under providers.azure_openai section")
            console.print("Use the model field to specify the deployment name.")
            raise typer.Exit(1)
        return AzureOpenAIProvider(
            api_key=provider_config.api_key,
            api_base=provider_config.api_base,
            default_model=model,
        )

    spec = find_by_name(provider_name)
    if (
        not model.startswith("bedrock/")
        and not (provider_config and provider_config.api_key)
        and not (spec and spec.is_oauth)
    ):
        console.print("[red]Error: No API key configured.[/red]")
        console.print("Set one in ~/.copala/config.json under providers section")
        raise typer.Exit(1)

    return LiteLLMProvider(
        api_key=provider_config.api_key if provider_config else None,
        api_base=api_base,
        default_model=model,
        extra_headers=provider_config.extra_headers if provider_config else None,
        provider_name=provider_name,
    )


def load_runtime_config(config: str | None = None, workspace: str | None = None) -> Config:
    """Load configuration and optionally override the active workspace."""
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


def calendar_service_from_config(config: Config):
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


def life_event_manager_from_config(config: Config):
    from copala.life_events.service import LifeEventManager

    return LifeEventManager(
        workspace=config.workspace_path,
        config=config.life_events,
        calendar_service=calendar_service_from_config(config),
        memory_store=None,
        cron_service=None,
        send_callback=None,
        mailbox_config=config.mailbox,
    )


def smart_reminder_service_from_config(config: Config, *, include_life_events: bool = True):
    from copala.smart_reminders.service import SmartReminderService

    return SmartReminderService(
        workspace=config.workspace_path,
        config=config.smart_reminders,
        calendar_service=calendar_service_from_config(config),
        life_events=life_event_manager_from_config(config) if include_life_events else None,
        daily_planning=None,
        memory_store=None,
        send_callback=None,
    )
