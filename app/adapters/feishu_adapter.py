"""Translate Feishu events into internal research-assistant tasks."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from app.router.task_router import route_task
from app.services.research_assistant_service import (
    ResearchAssistantResult,
    handle_research_assistant,
)


TextCleaner = Callable[[str], str]
ResearchAssistantHandler = Callable[..., Awaitable[ResearchAssistantResult]]
EventHandler = Callable[[Any], Awaitable[Any]]
MessageTransport = Callable[[str, str], Awaitable[Any]]

SENSITIVE_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b[A-Z0-9_]*(?:APP_ID|APP_SECRET|TABLE_ID|API_KEY)\b"
    r"\s*[:=]\s*\S+"
)


@dataclass(frozen=True, slots=True)
class ResearchTask:
    """Transport-neutral representation of one incoming Feishu message."""

    message_id: str
    message_type: str
    user_text: str
    raw_message: Mapping[str, Any]


class FeishuAdapter:
    """Convert Feishu payloads and dispatch text tasks to the assistant."""

    def __init__(
        self,
        assistant_handler: ResearchAssistantHandler = handle_research_assistant,
        send_handler: MessageTransport | None = None,
        reply_handler: MessageTransport | None = None,
    ) -> None:
        self._assistant_handler = assistant_handler
        self._send_handler = send_handler
        self._reply_handler = reply_handler

    def to_research_task(
        self,
        payload: Mapping[str, Any],
        text_cleaner: TextCleaner | None = None,
    ) -> ResearchTask:
        event = payload.get("event")
        event_data = event if isinstance(event, Mapping) else payload
        message = event_data.get("message")
        message_data = message if isinstance(message, Mapping) else event_data
        message_id = str(message_data.get("message_id") or "").strip()
        message_type = str(message_data.get("message_type") or "").strip()

        user_text = ""
        if message_type == "text":
            raw_content = message_data.get("content", "{}")
            try:
                content = (
                    json.loads(raw_content)
                    if isinstance(raw_content, str)
                    else raw_content
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                content = {"text": raw_content}
            if isinstance(content, Mapping):
                user_text = str(content.get("text") or "")
            cleaner = text_cleaner or (lambda value: value.strip())
            user_text = cleaner(user_text)

        return ResearchTask(
            message_id=message_id,
            message_type=message_type,
            user_text=user_text,
            raw_message=message_data,
        )

    def format_response(self, value: Any) -> str:
        """Normalize an assistant result and redact credential-shaped text."""
        if isinstance(value, ResearchAssistantResult):
            text = value.content
        elif isinstance(value, Mapping):
            text = next(
                (
                    str(value[key])
                    for key in ("result", "content", "message")
                    if value.get(key) is not None
                ),
                "",
            )
        else:
            text = str(value or "")
        normalized = text.strip() or "暂未生成有效结果，请稍后重试。"
        return SENSITIVE_ASSIGNMENT_PATTERN.sub("[敏感配置已隐藏]", normalized)

    async def send_message(
        self,
        receive_id: str,
        response: Any,
        *,
        sender: MessageTransport | None = None,
    ) -> Any:
        handler = sender or self._send_handler
        if handler is None:
            raise RuntimeError("飞书消息发送器未配置")
        return await handler(receive_id, self.format_response(response))

    async def reply_message(
        self,
        message_id: str,
        response: Any,
        *,
        replier: MessageTransport | None = None,
    ) -> Any:
        handler = replier or self._reply_handler
        if handler is None:
            raise RuntimeError("飞书消息回复器未配置")
        return await handler(message_id, self.format_response(response))

    async def dispatch(
        self,
        task: ResearchTask,
        *,
        kimi_handler: Any,
        deepseek_handler: Any,
        knowledge_provider: Any,
        deep_report_handler: Any,
        legacy_daily_handler: Any = None,
        fund_documents: Any = None,
        evidence_researcher: Any = None,
        routed_task: str | None = None,
    ) -> ResearchAssistantResult:
        """Call the unified Research Assistant without Feishu logic in services."""
        selected_task = routed_task or route_task(task.user_text)
        return await self._assistant_handler(
            task.user_text,
            kimi_handler=kimi_handler,
            deepseek_handler=deepseek_handler,
            knowledge_provider=knowledge_provider,
            deep_report_handler=deep_report_handler,
            legacy_daily_handler=legacy_daily_handler,
            fund_documents=fund_documents,
            evidence_researcher=evidence_researcher,
            routed_task=selected_task,
        )


def register_feishu_routes(application: Any, event_handler: EventHandler) -> None:
    """Register the Feishu HTTP transport on an application instance."""
    application.post("/feishu/events")(event_handler)
