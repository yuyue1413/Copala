"""Message bus module for decoupled channel-agent communication."""

from copala.bus.events import InboundMessage, OutboundMessage
from copala.bus.queue import MessageBus

__all__ = ["MessageBus", "InboundMessage", "OutboundMessage"]
