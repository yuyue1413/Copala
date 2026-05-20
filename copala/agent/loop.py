"""Agent loop: the core processing engine."""

from __future__ import annotations

import asyncio
import json
import re
import weakref
from contextlib import AsyncExitStack
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from copala.agent.context import ContextBuilder
from copala.agent.memory import MemoryStore
from copala.agent.subagent import SubagentManager
from copala.agent.tools.cron import CronTool
from copala.agent.tools.calendar import CalendarTool
from copala.agent.tools.daily_planning import DailyPlanningTool
from copala.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from copala.agent.tools.learned import LearnSkillTool
from copala.agent.tools.memory import MemoryTool
from copala.agent.tools.message import MessageTool
from copala.agent.tools.outing_planning import OutingPlanningTool
from copala.agent.tools.planning import ShoppingPlanTool, TravelPlanTool
from copala.agent.tools.registry import ToolRegistry
from copala.agent.tools.shell import ExecTool
from copala.agent.tools.smart_reminder import SmartReminderTool
from copala.agent.tools.spawn import SpawnTool
from copala.agent.tools.web import WebFetchTool, WebSearchTool
from copala.bus.events import InboundMessage, OutboundMessage
from copala.bus.queue import MessageBus
from copala.calendar.formatting import format_calendar_events
from copala.providers.base import LLMProvider
from copala.session.manager import Session, SessionManager

if TYPE_CHECKING:
    from copala.config.schema import CalendarConfig, ChannelsConfig, DailyPlanningConfig, ExecToolConfig, LifeEventsConfig, MailboxConfig, MemoryRAGConfig, OutingPlanningConfig, SmartRemindersConfig, WebSearchConfig
    from copala.cron.service import CronService


class AgentLoop:
    """
    The agent loop is the core processing engine.

    It:
    1. Receives messages from the bus
    2. Builds context with history, memory, skills
    3. Calls the LLM
    4. Executes tool calls
    5. Sends responses back
    """

    _TOOL_RESULT_MAX_CHARS = 500

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int = 40,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        memory_window: int = 100,
        reasoning_effort: str | None = None,
        brave_api_key: str | None = None,
        web_search_config: "WebSearchConfig | None" = None,
        web_proxy: str | None = None,
        exec_config: ExecToolConfig | None = None,
        cron_service: CronService | None = None,
        restrict_to_workspace: bool = False,
        session_manager: SessionManager | None = None,
        mcp_servers: dict | None = None,
        channels_config: ChannelsConfig | None = None,
        mailbox_config: "MailboxConfig | None" = None,
        memory_rag_config: "MemoryRAGConfig | None" = None,
        calendar_config: "CalendarConfig | None" = None,
        life_events_config: "LifeEventsConfig | None" = None,
        daily_planning_config: "DailyPlanningConfig | None" = None,
        outing_planning_config: "OutingPlanningConfig | None" = None,
        smart_reminders_config: "SmartRemindersConfig | None" = None,
    ):
        from copala.config.schema import ExecToolConfig
        self.bus = bus
        self.channels_config = channels_config
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.memory_window = memory_window
        self.reasoning_effort = reasoning_effort
        self.brave_api_key = brave_api_key
        self.web_search_config = web_search_config
        self.web_proxy = web_proxy
        self.exec_config = exec_config or ExecToolConfig()
        self.cron_service = cron_service
        self.restrict_to_workspace = restrict_to_workspace
        self.mailbox_config = mailbox_config
        self.memory_rag_config = memory_rag_config
        self.calendar_config = calendar_config
        self.life_events_config = life_events_config
        self.daily_planning_config = daily_planning_config
        self.outing_planning_config = outing_planning_config
        self.smart_reminders_config = smart_reminders_config

        self.context = ContextBuilder(workspace, memory_rag_config=memory_rag_config)
        self.sessions = session_manager or SessionManager(workspace)
        self.tools = ToolRegistry()
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            reasoning_effort=reasoning_effort,
            brave_api_key=brave_api_key,
            web_search_config=web_search_config,
            web_proxy=web_proxy,
            exec_config=self.exec_config,
            restrict_to_workspace=restrict_to_workspace,
        )

        self._running = False
        self._mcp_servers = mcp_servers or {}
        self._mcp_stack: AsyncExitStack | None = None
        self._mcp_connected = False
        self._mcp_connecting = False
        self._consolidating: set[str] = set()  # Session keys with consolidation in progress
        self._consolidation_tasks: set[asyncio.Task] = set()  # Strong refs to in-flight tasks
        self._consolidation_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
        self._active_tasks: dict[str, list[asyncio.Task]] = {}  # session_key -> tasks
        self._processing_lock = asyncio.Lock()
        self.memory_rag = self.context.memory_rag
        self.calendar_service = self._make_calendar_service()
        self.life_events = self._make_life_event_manager()
        self.daily_planning = self._make_daily_planning_service()
        self.smart_reminders = self._make_smart_reminder_service()
        self.outing_planning = self._make_outing_planning_service()
        self._register_default_tools()

    def _make_calendar_service(self):
        from copala.calendar.service import CalendarService
        cfg = self.calendar_config
        return CalendarService(
            self.workspace,
            ics_files=getattr(cfg, "ics_files", []),
            ics_urls=getattr(cfg, "ics_urls", []),
            local_json_files=getattr(cfg, "local_json_files", []),
            lookahead_days=getattr(cfg, "lookahead_days", 30),
            calendar_config=cfg,
            feishu_config=getattr(self.channels_config, "feishu", None),
        )

    def _make_life_event_manager(self):
        cfg = self.life_events_config
        if not cfg or not getattr(cfg, "enabled", False):
            return None
        from copala.life_events.service import LifeEventManager

        return LifeEventManager(
            workspace=self.workspace,
            config=cfg,
            calendar_service=self.calendar_service,
            memory_store=self.memory_rag,
            cron_service=self.cron_service,
            send_callback=self.bus.publish_outbound,
            mailbox_config=self.mailbox_config,
        )

    def _make_daily_planning_service(self):
        cfg = self.daily_planning_config
        if not cfg or not getattr(cfg, "enabled", True):
            return None
        from copala.planning.daily import DailyPlanningService

        return DailyPlanningService(
            config=cfg,
            calendar_service=self.calendar_service,
            life_events=self.life_events,
            memory_store=self.memory_rag,
        )

    def _make_smart_reminder_service(self):
        cfg = self.smart_reminders_config
        if not cfg or not getattr(cfg, "enabled", True):
            return None
        from copala.smart_reminders.service import SmartReminderService

        return SmartReminderService(
            workspace=self.workspace,
            config=cfg,
            calendar_service=self.calendar_service,
            life_events=self.life_events,
            daily_planning=self.daily_planning,
            memory_store=self.memory_rag,
            send_callback=self.bus.publish_outbound,
        )

    def _make_outing_planning_service(self):
        cfg = self.outing_planning_config
        if not cfg or not getattr(cfg, "enabled", True):
            return None
        from copala.planning.outing import OutingPlanningService

        amap_cfg = getattr(self.smart_reminders_config, "amap", None)
        return OutingPlanningService(
            config=cfg,
            calendar_service=self.calendar_service,
            memory_store=self.memory_rag,
            amap_config=amap_cfg,
            web_api_key=self.brave_api_key,
            web_search_config=self.web_search_config,
            web_proxy=self.web_proxy,
        )

    def _make_web_search_tool(self) -> WebSearchTool:
        cfg = self.web_search_config
        return WebSearchTool(
            api_key=(getattr(cfg, "api_key", "") or self.brave_api_key or None),
            max_results=int(getattr(cfg, "max_results", 5)),
            proxy=self.web_proxy,
            provider=str(getattr(cfg, "provider", "auto")),
            api_base=getattr(cfg, "api_base", "") or None,
            search_depth=str(getattr(cfg, "search_depth", "basic")),
            include_answer=bool(getattr(cfg, "include_answer", True)),
        )

    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        allowed_dir = self.workspace if self.restrict_to_workspace else None
        for cls in (ReadFileTool, WriteFileTool, EditFileTool, ListDirTool):
            self.tools.register(cls(workspace=self.workspace, allowed_dir=allowed_dir))
        self.tools.register(ExecTool(
            working_dir=str(self.workspace),
            timeout=self.exec_config.timeout,
            restrict_to_workspace=self.restrict_to_workspace,
            path_append=self.exec_config.path_append,
        ))
        self.tools.register(self._make_web_search_tool())
        self.tools.register(WebFetchTool(proxy=self.web_proxy))
        self.tools.register(MessageTool(send_callback=self.bus.publish_outbound))
        self.tools.register(SpawnTool(manager=self.subagents))
        self.tools.register(LearnSkillTool(self.workspace))
        if self.memory_rag:
            self.tools.register(MemoryTool(self.memory_rag))
            self.tools.register(TravelPlanTool(self.memory_rag, self.calendar_service))
            self.tools.register(ShoppingPlanTool(self.memory_rag))
        self.tools.register(CalendarTool(self.calendar_service))
        if self.daily_planning:
            self.tools.register(DailyPlanningTool(self.daily_planning))
        if self.smart_reminders:
            self.tools.register(SmartReminderTool(self.smart_reminders))
        if self.outing_planning:
            self.tools.register(OutingPlanningTool(self.outing_planning))
        if self.life_events:
            from copala.agent.tools.life_event import LifeEventTool
            self.tools.register(LifeEventTool(self.life_events))
        if self.cron_service:
            self.tools.register(CronTool(self.cron_service))
        if self.mailbox_config and self.mailbox_config.enabled and self.mailbox_config.username:
            from copala.agent.tools.email import EmailTool
            self.tools.register(EmailTool(self.mailbox_config, self.memory_rag, self.life_events))

    async def _connect_mcp(self) -> None:
        """Connect to configured MCP servers (one-time, lazy)."""
        if self._mcp_connected or self._mcp_connecting or not self._mcp_servers:
            return
        self._mcp_connecting = True
        from copala.agent.tools.mcp import connect_mcp_servers
        try:
            self._mcp_stack = AsyncExitStack()
            await self._mcp_stack.__aenter__()
            await connect_mcp_servers(self._mcp_servers, self.tools, self._mcp_stack)
            self._mcp_connected = True
        except Exception as e:
            logger.error("Failed to connect MCP servers (will retry next message): {}", e)
            if self._mcp_stack:
                try:
                    await self._mcp_stack.aclose()
                except Exception:
                    pass
                self._mcp_stack = None
        finally:
            self._mcp_connecting = False

    def _set_tool_context(self, channel: str, chat_id: str, message_id: str | None = None) -> None:
        """Update context for all tools that need routing info."""
        for name in ("message", "spawn", "cron", "email"):
            if tool := self.tools.get(name):
                if hasattr(tool, "set_context"):
                    tool.set_context(channel, chat_id, *([message_id] if name == "message" else []))

    @staticmethod
    def _strip_think(text: str | None) -> str | None:
        """Remove <think>…</think> blocks that some models embed in content."""
        if not text:
            return None
        return re.sub(r"<think>[\s\S]*?</think>", "", text).strip() or None

    @staticmethod
    def _tool_hint(tool_calls: list) -> str:
        """Format tool calls as concise hint, e.g. 'web_search("query")'."""
        def _fmt(tc):
            args = (tc.arguments[0] if isinstance(tc.arguments, list) else tc.arguments) or {}
            val = next(iter(args.values()), None) if isinstance(args, dict) else None
            if not isinstance(val, str):
                return tc.name
            return f'{tc.name}("{val[:40]}…")' if len(val) > 40 else f'{tc.name}("{val}")'
        return ", ".join(_fmt(tc) for tc in tool_calls)

    async def _run_agent_loop(
        self,
        initial_messages: list[dict],
        on_progress: Callable[..., Awaitable[None]] | None = None,
    ) -> tuple[str | None, list[str], list[dict]]:
        """Run the agent iteration loop. Returns (final_content, tools_used, messages)."""
        messages = initial_messages
        iteration = 0
        final_content = None
        tools_used: list[str] = []

        while iteration < self.max_iterations:
            iteration += 1

            response = await self.provider.chat(
                messages=messages,
                tools=self.tools.get_definitions(),
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                reasoning_effort=self.reasoning_effort,
            )

            if response.has_tool_calls:
                if on_progress:
                    thought = self._strip_think(response.content)
                    if thought:
                        await on_progress(thought)
                    await on_progress(self._tool_hint(response.tool_calls), tool_hint=True)

                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False)
                        }
                    }
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages, response.content, tool_call_dicts,
                    reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )

                for tool_call in response.tool_calls:
                    tools_used.append(tool_call.name)
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                    logger.info("Tool call: {}({})", tool_call.name, args_str[:200])
                    result = await self.tools.execute(tool_call.name, tool_call.arguments)
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
            else:
                clean = self._strip_think(response.content)
                # Don't persist error responses to session history — they can
                # poison the context and cause permanent 400 loops (#1303).
                if response.finish_reason == "error":
                    logger.error("LLM returned error: {}", clean or "")
                    final_content = self._safe_user_error(clean)
                    break
                messages = self.context.add_assistant_message(
                    messages, clean, reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )
                final_content = clean
                break

        if final_content is None and iteration >= self.max_iterations:
            logger.warning("Max iterations ({}) reached", self.max_iterations)
            final_content = (
                f"I reached the maximum number of tool call iterations ({self.max_iterations}) "
                "without completing the task. You can try breaking the task into smaller steps."
            )

        return final_content, tools_used, messages

    async def run(self) -> None:
        """Run the agent loop, dispatching messages as tasks to stay responsive to /stop."""
        self._running = True
        await self._connect_mcp()
        logger.info("Agent loop started")

        while self._running:
            try:
                msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            if msg.content.strip().lower() == "/stop":
                await self._handle_stop(msg)
            else:
                task = asyncio.create_task(self._dispatch(msg))
                self._active_tasks.setdefault(msg.session_key, []).append(task)
                task.add_done_callback(lambda t, k=msg.session_key: self._active_tasks.get(k, []) and self._active_tasks[k].remove(t) if t in self._active_tasks.get(k, []) else None)

    async def _handle_stop(self, msg: InboundMessage) -> None:
        """Cancel all active tasks and subagents for the session."""
        tasks = self._active_tasks.pop(msg.session_key, [])
        cancelled = sum(1 for t in tasks if not t.done() and t.cancel())
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        sub_cancelled = await self.subagents.cancel_by_session(msg.session_key)
        total = cancelled + sub_cancelled
        content = f"⏹ Stopped {total} task(s)." if total else "No active task to stop."
        await self.bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=content,
        ))

    async def _dispatch(self, msg: InboundMessage) -> None:
        """Process a message under the global lock."""
        async with self._processing_lock:
            try:
                response = await self._process_message(msg)
                if response is not None:
                    await self.bus.publish_outbound(response)
                elif msg.channel == "cli":
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel, chat_id=msg.chat_id,
                        content="", metadata=msg.metadata or {},
                    ))
            except asyncio.CancelledError:
                logger.info("Task cancelled for session {}", msg.session_key)
                raise
            except Exception:
                logger.exception("Error processing message for session {}", msg.session_key)
                await self.bus.publish_outbound(OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content="Sorry, I encountered an error.",
                ))

    async def close_mcp(self) -> None:
        """Close MCP connections."""
        if self._mcp_stack:
            try:
                await self._mcp_stack.aclose()
            except (RuntimeError, BaseExceptionGroup):
                pass  # MCP SDK cancel scope cleanup is noisy but harmless
            self._mcp_stack = None

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")

    async def _process_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> OutboundMessage | None:
        """Process a single inbound message and return the response."""
        # System messages: parse origin from chat_id ("channel:chat_id")
        if msg.channel == "system":
            channel, chat_id = (msg.chat_id.split(":", 1) if ":" in msg.chat_id
                                else ("cli", msg.chat_id))
            logger.info("Processing system message from {}", msg.sender_id)
            key = f"{channel}:{chat_id}"
            session = self.sessions.get_or_create(key)
            self._set_tool_context(channel, chat_id, msg.metadata.get("message_id"))
            history = session.get_history(max_messages=self.memory_window)
            messages = self.context.build_messages(
                history=history,
                current_message=msg.content, channel=channel, chat_id=chat_id,
            )
            final_content, _, all_msgs = await self._run_agent_loop(messages)
            self._save_turn(session, all_msgs, 1 + len(history))
            self.sessions.save(session)
            return OutboundMessage(channel=channel, chat_id=chat_id,
                                  content=final_content or "Background task completed.")

        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info("Processing message from {}:{}: {}", msg.channel, msg.sender_id, preview)

        key = session_key or msg.session_key
        session = self.sessions.get_or_create(key)

        # Slash commands
        cmd = msg.content.strip().lower()
        if cmd == "/new":
            lock = self._consolidation_locks.setdefault(session.key, asyncio.Lock())
            self._consolidating.add(session.key)
            try:
                async with lock:
                    snapshot = session.messages[session.last_consolidated:]
                    if snapshot:
                        temp = Session(key=session.key)
                        temp.messages = list(snapshot)
                        if not await self._consolidate_memory(temp, archive_all=True):
                            return OutboundMessage(
                                channel=msg.channel, chat_id=msg.chat_id,
                                content="Memory archival failed, session not cleared. Please try again.",
                            )
            except Exception:
                logger.exception("/new archival failed for {}", session.key)
                return OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content="Memory archival failed, session not cleared. Please try again.",
                )
            finally:
                self._consolidating.discard(session.key)

            session.clear()
            self.sessions.save(session)
            self.sessions.invalidate(session.key)
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id,
                                  content="New session started.")
        if cmd == "/help":
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id,
                                  content="🐈 copala commands:\n/new — Start a new conversation\n/stop — Stop the current task\n/help — Show available commands")

        direct_calendar = await self._direct_calendar_query(msg)
        if direct_calendar is not None:
            session.add_message("user", msg.content)
            session.add_message("assistant", direct_calendar)
            self.sessions.save(session)
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=direct_calendar)

        direct_outing_plan = await self._direct_outing_planning(msg)
        if direct_outing_plan is not None:
            session.add_message("user", msg.content)
            session.add_message("assistant", direct_outing_plan)
            self.sessions.save(session)
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=direct_outing_plan)

        direct_smart_reminder = await self._direct_smart_reminder(msg)
        if direct_smart_reminder is not None:
            session.add_message("user", msg.content)
            session.add_message("assistant", direct_smart_reminder)
            self.sessions.save(session)
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=direct_smart_reminder)

        direct_daily_plan = await self._direct_daily_planning(msg)
        if direct_daily_plan is not None:
            session.add_message("user", msg.content)
            session.add_message("assistant", direct_daily_plan)
            self.sessions.save(session)
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=direct_daily_plan)

        life_event_response = await self._auto_process_life_events(msg)
        if life_event_response:
            session.add_message("user", msg.content)
            session.add_message("assistant", life_event_response)
            self.sessions.save(session)
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=life_event_response)

        unconsolidated = len(session.messages) - session.last_consolidated
        if (unconsolidated >= self.memory_window and session.key not in self._consolidating):
            self._consolidating.add(session.key)
            lock = self._consolidation_locks.setdefault(session.key, asyncio.Lock())

            async def _consolidate_and_unlock():
                try:
                    async with lock:
                        await self._consolidate_memory(session)
                finally:
                    self._consolidating.discard(session.key)
                    _task = asyncio.current_task()
                    if _task is not None:
                        self._consolidation_tasks.discard(_task)

            _task = asyncio.create_task(_consolidate_and_unlock())
            self._consolidation_tasks.add(_task)

        self._set_tool_context(msg.channel, msg.chat_id, msg.metadata.get("message_id"))
        if message_tool := self.tools.get("message"):
            if isinstance(message_tool, MessageTool):
                message_tool.start_turn()

        history = session.get_history(max_messages=self.memory_window)
        initial_messages = self.context.build_messages(
            history=history,
            current_message=msg.content,
            media=msg.media if msg.media else None,
            channel=msg.channel, chat_id=msg.chat_id,
        )

        async def _bus_progress(content: str, *, tool_hint: bool = False) -> None:
            meta = dict(msg.metadata or {})
            meta["_progress"] = True
            meta["_tool_hint"] = tool_hint
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id, content=content, metadata=meta,
            ))

        final_content, tools_used, all_msgs = await self._run_agent_loop(
            initial_messages, on_progress=on_progress or _bus_progress,
        )

        if final_content is None:
            final_content = "I've completed processing but have no response to give."
        final_content = self._sanitize_user_facing_content(final_content)
        if self._looks_like_unverified_calendar_write_claim(final_content, tools_used):
            logger.warning("Blocked unverified calendar write claim: {}", final_content[:200])
            final_content = (
                "我识别到这可能是一个日程，但刚才没有完成真实日历写入。"
                "请再发一次包含明确时间和事件的消息，我会通过日程工具写入飞书日历。"
            )

        self._save_turn(session, all_msgs, 1 + len(history))
        self._auto_extract_turn_memory(msg)
        self.sessions.save(session)

        if (mt := self.tools.get("message")) and isinstance(mt, MessageTool) and mt._sent_in_turn:
            return None

        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info("Response to {}:{}: {}", msg.channel, msg.sender_id, preview)
        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=final_content,
            metadata=msg.metadata or {},
        )

    async def _direct_calendar_query(self, msg: InboundMessage) -> str | None:
        if msg.channel != "qq":
            return None
        text = msg.content.strip()
        if self._looks_like_calendar_status_query(text):
            return self._format_calendar_status(self.calendar_service.auth_status())
        if not self._looks_like_readonly_calendar_query(text):
            return None

        start_date, end_date, label = self._calendar_query_range(text)
        try:
            events = await self.calendar_service.list_events_async(
                start_date=start_date,
                end_date=end_date,
                source="feishu",
            )
        except Exception as exc:
            logger.exception("Direct Feishu calendar query failed")
            return f"飞书日历访问失败：{exc}"
        return self._format_direct_calendar_events(events, label)

    async def _direct_daily_planning(self, msg: InboundMessage) -> str | None:
        if not self.daily_planning or msg.channel not in {"qq", "feishu"}:
            return None
        text = msg.content.strip()
        if not self._looks_like_daily_planning_query(text):
            return None
        try:
            mode = "reschedule" if self._looks_like_replanning_query(text) else "plan_day"
            return await self.daily_planning.plan_day(text, mode=mode)
        except Exception:
            logger.exception("Direct daily planning failed")
            return "我这边生成主动规划时遇到了一点问题，已经记录到后台日志里了。"

    async def _direct_smart_reminder(self, msg: InboundMessage) -> str | None:
        if not self.smart_reminders or msg.channel not in {"qq", "feishu"}:
            return None
        text = msg.content.strip()
        if not self._looks_like_smart_reminder_query(text):
            return None
        try:
            days = 2
            if any(word in text for word in ("一周", "7天", "七天", "week")):
                days = 7
            risks = await self.smart_reminders.check_risks(days=days)
            return self.smart_reminders.format_risks(risks)
        except Exception:
            logger.exception("Direct smart reminder risk check failed")
            return "我检查天气、交通或截止风险时遇到了一点问题，已经记录到后台日志里了。"

    @staticmethod
    def _looks_like_smart_reminder_query(text: str) -> bool:
        lowered = text.lower()
        ascii_outing_words = (
            "\u65c5\u884c", "\u65c5\u6e38", "\u51fa\u6e38", "\u5468\u672b", "\u5468\u672b\u6e38",
            "\u5468\u672b\u53bb", "\u53bb\u5546\u573a", "\u901b\u5546\u573a",
            "\u4e34\u65f6\u5916\u51fa", "\u524d\u5f80", "\u8def\u7ebf\u89c4\u5212", "\u884c\u7a0b",
            "\u73a9", "\u901b",
        )
        ascii_destination_words = (
            "\u53bb", "\u5230", "\u524d\u5f80", "\u5546\u573a", "\u676d\u5dde", "\u82cf\u5dde",
            "\u4e0a\u6d77", "\u5357\u4eac", "\u5317\u4eac", "\u6210\u90fd", "\u5e7f\u5dde", "\u6df1\u5733",
        )
        ascii_write_words = ("\u5199\u5165\u65e5\u5386", "\u521b\u5efa\u65e5\u7a0b", "\u52a0\u5165\u65e5\u5386", "\u6539\u5230")
        if any(word in text for word in ascii_write_words):
            return False
        ascii_has_destination = any(word in text for word in ascii_destination_words) or any(
            word in lowered for word in ("to ", "hangzhou", "suzhou", "mall")
        )
        if (
            any(word in text for word in ascii_outing_words)
            or any(word in lowered for word in ("trip", "travel", "outing", "mall", "itinerary"))
        ) and ascii_has_destination:
            return True
        risk_words = ("风险", "预警", "提醒", "下雨", "天气", "交通", "堵车", "路线", "来得及", "冲突")
        action_words = ("检查", "看看", "有没有", "会不会", "影响", "调整", "预警")
        return (
            any(word in text for word in risk_words) or any(word in lowered for word in ("risk", "weather", "traffic"))
        ) and (
            any(word in text for word in action_words) or any(word in lowered for word in ("check", "warn", "alert"))
        )

    async def _direct_outing_planning(self, msg: InboundMessage) -> str | None:
        if not self.outing_planning or msg.channel not in {"qq", "feishu"}:
            return None
        text = msg.content.strip()
        if not self._looks_like_outing_planning_query(text):
            return None
        try:
            return await self.outing_planning.plan_from_request(text)
        except Exception:
            logger.exception("Direct outing planning failed")
            return "我生成出行规划时遇到了一点问题，已经记录到后台日志里了。"

    @staticmethod
    def _looks_like_outing_planning_query(text: str) -> bool:
        lowered = text.lower()
        outing_words = ("旅行", "旅游", "出游", "周末游", "周末去", "去商场", "逛商场", "临时外出", "前往", "路线规划", "行程")
        destination_words = ("去", "到", "前往", "商场", "杭州", "苏州", "上海", "南京", "北京", "成都", "广州", "深圳")
        write_words = ("写入日历", "创建日程", "加入日历", "改到")
        if any(word in text for word in write_words):
            return False
        has_destination = any(word in text for word in destination_words) or any(
            word in lowered for word in ("to ", "hangzhou", "suzhou", "mall")
        )
        return (
            any(word in text for word in outing_words)
            or any(word in lowered for word in ("trip", "travel", "outing", "mall", "itinerary"))
        ) and has_destination

    @staticmethod
    def _looks_like_outing_planning_query(text: str) -> bool:
        lowered = text.lower()
        outing_words = (
            "\u65c5\u884c", "\u65c5\u6e38", "\u51fa\u6e38", "\u5468\u672b\u6e38",
            "\u5468\u672b\u53bb", "\u53bb\u5546\u573a", "\u901b\u5546\u573a",
            "\u4e34\u65f6\u5916\u51fa", "\u524d\u5f80", "\u8def\u7ebf\u89c4\u5212", "\u884c\u7a0b",
        )
        destination_words = (
            "\u53bb", "\u5230", "\u524d\u5f80", "\u5546\u573a", "\u676d\u5dde", "\u82cf\u5dde",
            "\u4e0a\u6d77", "\u5357\u4eac", "\u5317\u4eac", "\u6210\u90fd", "\u5e7f\u5dde", "\u6df1\u5733",
        )
        write_words = ("\u5199\u5165\u65e5\u5386", "\u521b\u5efa\u65e5\u7a0b", "\u52a0\u5165\u65e5\u5386", "\u6539\u5230")
        if any(word in text for word in write_words):
            return False
        has_destination = any(word in text for word in destination_words) or any(
            word in lowered for word in ("to ", "hangzhou", "suzhou", "mall")
        )
        return (
            any(word in text for word in outing_words)
            or any(word in lowered for word in ("trip", "travel", "outing", "mall", "itinerary"))
        ) and has_destination

    @staticmethod
    def _looks_like_smart_reminder_query(text: str) -> bool:
        lowered = text.lower()
        if AgentLoop._looks_like_outing_planning_query(text):
            return False
        risk_words = (
            "\u98ce\u9669", "\u9884\u8b66", "\u63d0\u9192", "\u4e0b\u96e8", "\u5929\u6c14",
            "\u4ea4\u901a", "\u5835\u8f66", "\u8def\u7ebf", "\u6765\u5f97\u53ca", "\u51b2\u7a81",
        )
        action_words = (
            "\u68c0\u67e5", "\u770b\u770b", "\u6709\u6ca1\u6709", "\u4f1a\u4e0d\u4f1a",
            "\u5f71\u54cd", "\u8c03\u6574", "\u9884\u8b66",
        )
        return (
            any(word in text for word in risk_words) or any(word in lowered for word in ("risk", "weather", "traffic"))
        ) and (
            any(word in text for word in action_words) or any(word in lowered for word in ("check", "warn", "alert"))
        )

    @staticmethod
    def _looks_like_outing_planning_query(text: str) -> bool:
        lowered = text.lower()
        outing_words = (
            "\u65c5\u884c", "\u65c5\u6e38", "\u51fa\u6e38", "\u5468\u672b", "\u5468\u672b\u6e38",
            "\u5468\u672b\u53bb", "\u53bb\u5546\u573a", "\u901b\u5546\u573a", "\u4e34\u65f6\u5916\u51fa",
            "\u524d\u5f80", "\u8def\u7ebf\u89c4\u5212", "\u884c\u7a0b", "\u73a9", "\u901b",
        )
        destination_words = (
            "\u53bb", "\u5230", "\u524d\u5f80", "\u5546\u573a", "\u676d\u5dde", "\u82cf\u5dde",
            "\u4e0a\u6d77", "\u5357\u4eac", "\u5317\u4eac", "\u6210\u90fd", "\u5e7f\u5dde", "\u6df1\u5733",
        )
        write_words = ("\u5199\u5165\u65e5\u5386", "\u521b\u5efa\u65e5\u7a0b", "\u52a0\u5165\u65e5\u5386", "\u6539\u5230")
        if any(word in text for word in write_words):
            return False
        has_destination = any(word in text for word in destination_words) or any(
            word in lowered for word in ("to ", "hangzhou", "suzhou", "mall")
        )
        return (
            any(word in text for word in outing_words)
            or any(word in lowered for word in ("trip", "travel", "outing", "mall", "itinerary"))
        ) and has_destination

    @staticmethod
    def _looks_like_daily_planning_query(text: str) -> bool:
        lowered = text.lower()
        plan_words = ("安排", "规划", "计划", "重新安排", "重新规划", "最优安排", "顺路", "空档")
        schedule_words = ("今天", "明天", "后天", "日程", "会议", "待办", "快递", "买药", "回复邮件")
        write_words = ("写入", "创建", "修改飞书", "加入日历", "记到日历")
        if any(word in text for word in write_words):
            return False
        return (
            any(word in text for word in plan_words) or any(word in lowered for word in ("plan", "schedule", "reschedule"))
        ) and any(word in text for word in schedule_words)

    @staticmethod
    def _looks_like_replanning_query(text: str) -> bool:
        return any(word in text for word in ("重新安排", "重新规划", "改期", "延迟", "推迟", "提前", "变更"))

    @staticmethod
    def _looks_like_calendar_status_query(text: str) -> bool:
        lowered = text.lower()
        has_calendar = any(word in text for word in ("日历", "日程", "飞书")) or "calendar" in lowered
        has_status = any(word in text for word in ("认证", "授权", "状态", "配置", "检查")) or "status" in lowered
        return has_calendar and has_status and not any(word in text for word in ("创建", "修改", "更改", "删除"))

    @staticmethod
    def _looks_like_readonly_calendar_query(text: str) -> bool:
        lowered = text.lower()
        calendar_words = ("日程", "飞书日程", "飞书日历", "calendar", "schedule")
        date_words = ("今天", "今日", "明天", "本周", "这周", "today", "tomorrow", "week")
        query_words = ("查", "查看", "查询", "看看", "看下", "列出", "有什么", "有没有", "list", "show")
        write_or_plan_words = (
            "创建", "新建", "修改", "更改", "移动", "移到", "删除", "取消",
            "提醒", "规划", "建议", "根据", "安排写", "安排一个",
        )
        if any(word in text for word in write_or_plan_words):
            return False
        has_calendar = any(word in text for word in calendar_words) or any(word in lowered for word in calendar_words)
        has_date = any(word in text for word in date_words) or any(word in lowered for word in date_words)
        has_query = any(word in text for word in query_words) or any(word in lowered for word in query_words)
        return has_calendar and has_date and (has_query or len(text) <= 20)

    @staticmethod
    def _calendar_query_range(text: str) -> tuple[date, date, str]:
        today = date.today()
        lowered = text.lower()
        if "明天" in text or "tomorrow" in lowered:
            tomorrow = today + timedelta(days=1)
            return tomorrow, tomorrow, "明天"
        if "本周" in text or "这周" in text or "week" in lowered:
            sunday = today + timedelta(days=6 - today.weekday())
            return today, sunday, "本周"
        return today, today, "今天"

    @staticmethod
    def _format_direct_calendar_events(events: list[Any], label: str) -> str:
        return format_calendar_events(events, label=f"{label}飞书日程", limit=20)

    @staticmethod
    def _format_calendar_status(status: dict[str, Any]) -> str:
        provider = status.get("providers", {}).get("feishu", {})
        if not provider:
            return "飞书日历未配置：calendar.enterpriseEnabled 或 calendar.providers.feishu.enabled 未开启。"
        enabled = "已启用" if status.get("enterprise_enabled") else "未启用"
        token_exists = "已存在" if provider.get("token_file_exists") else "不存在"
        app_id = "已配置" if provider.get("has_app_id") else "缺失"
        app_secret = "已配置" if provider.get("has_app_secret") else "缺失"
        return "\n".join([
            "飞书日历认证状态：",
            f"- 企业日历：{enabled}",
            f"- authMode：{provider.get('auth_mode') or '未设置'}",
            f"- App ID：{app_id}",
            f"- App Secret：{app_secret}",
            f"- Token 文件：{token_exists}",
            f"- 默认日历：{provider.get('default_calendar_id') or 'primary'}",
        ])

    def _auto_extract_turn_memory(self, msg: InboundMessage) -> None:
        if not self.memory_rag or not getattr(self.memory_rag_config, "auto_extract", True):
            return
        try:
            from copala.agent.memory_rag.extractor import MemoryExtractor

            source = "email" if msg.channel == "email" else "conversation"
            items = MemoryExtractor().extract(
                msg.content,
                source=source,
                source_ref=f"{msg.channel}:{msg.chat_id}",
            )
            if items:
                self.memory_rag.upsert_many(items)
        except Exception:
            logger.debug("Turn memory extraction skipped", exc_info=True)

    async def _auto_process_life_events(self, msg: InboundMessage) -> str | None:
        if not self.life_events or msg.channel in {"cli", "system"}:
            return None
        try:
            message_id = str((msg.metadata or {}).get("message_id") or "")
            if not message_id:
                digest = abs(hash((msg.channel, msg.chat_id, msg.content, msg.timestamp.isoformat())))
                message_id = str(digest)
            result = await self.life_events.process_text(
                msg.content,
                source=msg.channel,
                source_ref=f"{msg.channel}:{msg.chat_id}:{message_id}",
                notify_channel=msg.channel,
                notify_chat_id=msg.chat_id,
                send_notifications=False,
            )
            if result.messages:
                return "\n\n".join(result.messages)
            return None
        except Exception:
            logger.warning("Life event processing skipped", exc_info=True)
            return None

    def _save_turn(self, session: Session, messages: list[dict], skip: int) -> None:
        """Save new-turn messages into session, truncating large tool results."""
        from datetime import datetime
        for m in messages[skip:]:
            entry = dict(m)
            role, content = entry.get("role"), entry.get("content")
            if role == "assistant" and not content and not entry.get("tool_calls"):
                continue  # skip empty assistant messages — they poison session context
            if role == "tool" and isinstance(content, str) and len(content) > self._TOOL_RESULT_MAX_CHARS:
                entry["content"] = content[:self._TOOL_RESULT_MAX_CHARS] + "\n... (truncated)"
            elif role == "user":
                if isinstance(content, str) and content.startswith(ContextBuilder._RUNTIME_CONTEXT_TAG):
                    # Strip the runtime-context prefix, keep only the user text.
                    parts = content.split("\n\n", 1)
                    if len(parts) > 1 and parts[1].strip():
                        entry["content"] = parts[1]
                    else:
                        continue
                if isinstance(content, list):
                    filtered = []
                    for c in content:
                        if c.get("type") == "text" and isinstance(c.get("text"), str) and c["text"].startswith(ContextBuilder._RUNTIME_CONTEXT_TAG):
                            continue  # Strip runtime context from multimodal messages
                        if (c.get("type") == "image_url"
                                and c.get("image_url", {}).get("url", "").startswith("data:image/")):
                            filtered.append({"type": "text", "text": "[image]"})
                        else:
                            filtered.append(c)
                    if not filtered:
                        continue
                    entry["content"] = filtered
            entry.setdefault("timestamp", datetime.now().isoformat())
            session.messages.append(entry)
        session.updated_at = datetime.now()

    async def _consolidate_memory(self, session, archive_all: bool = False) -> bool:
        """Delegate to MemoryStore.consolidate(). Returns True on success."""
        return await MemoryStore(self.workspace).consolidate(
            session, self.provider, self.model,
            archive_all=archive_all, memory_window=self.memory_window,
        )

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        """Process a message directly (for CLI or cron usage)."""
        await self._connect_mcp()
        msg = InboundMessage(channel=channel, sender_id="user", chat_id=chat_id, content=content)
        response = await self._process_message(msg, session_key=session_key, on_progress=on_progress)
        return response.content if response else ""

    @classmethod
    def _sanitize_user_facing_content(cls, content: str) -> str:
        """Hide backend/provider details from normal chat responses."""
        text = content or ""
        if cls._looks_like_backend_error(text):
            return cls._safe_user_error(text)
        return text

    @classmethod
    def _safe_user_error(cls, raw: str | None = None) -> str:
        """Return a friendly error while raw details stay in logs."""
        text = raw or ""
        lowered = text.lower()
        if "429" in text or "quota" in lowered or "rate limit" in lowered or "rate_limit" in lowered:
            return "我这边的模型服务暂时额度不足或请求过于频繁，稍后再试一下就好。"
        if "timeout" in lowered or "timed out" in lowered:
            return "我这边响应超时了，稍后再试一下。"
        return "我这边处理时遇到了一点问题，已经记录到后台日志里了。"

    @staticmethod
    def _looks_like_backend_error(content: str) -> bool:
        lowered = content.lower()
        sensitive_markers = (
            "error code:",
            "traceback",
            "stack trace",
            "api_key",
            "apikey",
            "authorization",
            "bearer ",
            "quota",
            "rate limit",
            "rate_limit",
            "litellm",
            "openai",
            "deepseek-ai/",
            "httpstatuserror",
            "client error",
            "server error",
        )
        if lowered.startswith(("error:", "exception:", "traceback")):
            return True
        return any(marker in lowered for marker in sensitive_markers)

    @staticmethod
    def _looks_like_unverified_calendar_write_claim(content: str, tools_used: list[str]) -> bool:
        if any(tool in {"calendar", "life_event"} for tool in tools_used):
            return False
        text = content or ""
        has_calendar = any(word in text for word in ("日程", "日历", "飞书日历", "飞书"))
        has_write_claim = any(
            word in text
            for word in (
                "已写入", "写入成功", "已创建", "创建成功", "已记录",
                "已经记录", "安排好了", "已安排", "添加成功",
            )
        )
        return has_calendar and has_write_claim
