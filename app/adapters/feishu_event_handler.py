"""Feishu event orchestration for the standalone Research Assistant service."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from app.adapters.feishu_adapter import FeishuAdapter
from app.router.task_router import (
    DAILY_REPORT,
    FUND_ANALYSIS,
    GENERAL_CHAT,
    RESEARCH_REPORT,
    route_task,
)

if TYPE_CHECKING:
    from app.bootstrap import ApplicationServices


UNRECOGNIZED_MESSAGE = (
    "暂未识别研究任务。你可以发送：分析XX基金、生成投研日报，"
    "或写一篇关于XX的深度研究报告。"
)
PUBLIC_TASK_NAMES = {
    FUND_ANALYSIS: "fund_analysis",
    DAILY_REPORT: "daily_report",
    RESEARCH_REPORT: "research_report",
}


def route_feishu_message(user_text: str) -> str | None:
    """Limit the Feishu event entry to the three explicitly supported tasks."""
    normalized = str(user_text or "").strip()
    routed_task = route_task(normalized)
    if routed_task in PUBLIC_TASK_NAMES:
        return routed_task
    if "日报" in normalized:
        return DAILY_REPORT
    if any(marker in normalized for marker in ("深度研究", "研究报告")):
        return RESEARCH_REPORT
    if routed_task == GENERAL_CHAT:
        return None
    return None


class FeishuEventHandler:
    def __init__(
        self,
        services: ApplicationServices,
        runtime: Any = None,
    ) -> None:
        self.services = services
        self.adapter = services.feishu_adapter
        self.runtime = runtime

    def _runtime(self):
        if self.runtime is None:
            from app.server import load_runtime_dependencies

            self.runtime = load_runtime_dependencies()
        return self.runtime

    async def handle_event(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if payload.get("type") == "url_verification":
            return {"challenge": payload.get("challenge", "")}

        task = self.adapter.to_research_task(payload)
        if task.message_type != "text":
            return {
                "code": 0,
                "status": "unsupported",
                "message": "当前仅支持文本消息。",
            }
        if not task.user_text:
            return {
                "code": 0,
                "status": "invalid",
                "message": "消息内容为空，请输入研究任务。",
            }

        routed_task = route_feishu_message(task.user_text)
        if routed_task is None:
            return {
                "code": 0,
                "status": "unrecognized",
                "message": UNRECOGNIZED_MESSAGE,
            }

        runtime = self._runtime()
        try:
            result = await self.adapter.dispatch(
                task,
                kimi_handler=runtime.kimi_handler,
                deepseek_handler=runtime.deepseek_handler,
                knowledge_provider=runtime.knowledge_provider,
                deep_report_handler=runtime.deep_report_handler,
                legacy_daily_handler=runtime.legacy_daily_handler,
                evidence_researcher=self.services.public_search_provider.search,
                routed_task=routed_task,
            )
            response_text = self.adapter.format_response(result)
            reply_handler = getattr(runtime, "reply_message_handler", None)
            if task.message_id and reply_handler is not None:
                await self.adapter.reply_message(
                    task.message_id,
                    response_text,
                    replier=reply_handler,
                )
            return {
                "code": 0,
                "status": "ok",
                "task_type": PUBLIC_TASK_NAMES[routed_task],
                "result": response_text,
            }
        except Exception:
            return {
                "code": 1,
                "status": "error",
                "message": "研究任务处理失败，请稍后重试。",
            }


def create_feishu_event_handler(
    services: ApplicationServices,
    runtime: Any = None,
):
    handler = FeishuEventHandler(services, runtime)

    async def endpoint(request_or_payload: Any) -> dict[str, Any]:
        if isinstance(request_or_payload, Mapping):
            payload = request_or_payload
        else:
            payload = await request_or_payload.json()
        if not isinstance(payload, Mapping):
            return {
                "code": 1,
                "status": "error",
                "message": "飞书事件格式无效。",
            }
        return await handler.handle_event(payload)

    return endpoint
