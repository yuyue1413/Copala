"""LLM provider abstractions with lazy concrete-provider imports."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from copala.providers.base import LLMProvider, LLMResponse

if TYPE_CHECKING:
    from copala.providers.azure_openai_provider import AzureOpenAIProvider
    from copala.providers.litellm_provider import LiteLLMProvider
    from copala.providers.openai_codex_provider import OpenAICodexProvider

__all__ = [
    "LLMProvider",
    "LLMResponse",
    "LiteLLMProvider",
    "OpenAICodexProvider",
    "AzureOpenAIProvider",
]

_LAZY_IMPORTS = {
    "LiteLLMProvider": ("copala.providers.litellm_provider", "LiteLLMProvider"),
    "OpenAICodexProvider": ("copala.providers.openai_codex_provider", "OpenAICodexProvider"),
    "AzureOpenAIProvider": ("copala.providers.azure_openai_provider", "AzureOpenAIProvider"),
}


def __getattr__(name: str) -> Any:
    """Load concrete providers only when the public attribute is requested."""
    try:
        module_name, attribute = _LAZY_IMPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value
