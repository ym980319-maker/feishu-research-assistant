from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import AsyncMock

from app.adapters.feishu_adapter import FeishuAdapter
from app.adapters.feishu_event_handler import (
    UNRECOGNIZED_MESSAGE,
    create_feishu_event_handler,
    route_feishu_message,
)
from app.bootstrap import initialize_services
from app.config import load_config
from app.models.research_task import ResearchTaskType
from app.router.task_router import DAILY_REPORT, FUND_ANALYSIS, RESEARCH_REPORT
from app.server import RuntimeDependencies
from app.services.research_assistant_service import ResearchAssistantResult


def _runtime(*, reply_handler=None) -> RuntimeDependencies:
    return RuntimeDependencies(
        kimi_handler=AsyncMock(return_value="Kimi 结果"),
        deepseek_handler=AsyncMock(return_value="DeepSeek 结果"),
        knowledge_provider=AsyncMock(return_value="知识库"),
        deep_report_handler=AsyncMock(return_value="深度报告"),
        legacy_daily_handler=AsyncMock(return_value="日报"),
        reply_message_handler=reply_handler,
    )


class FeishuAdapterMessageTests(unittest.IsolatedAsyncioTestCase):
    def test_parses_flat_text_message(self) -> None:
        adapter = FeishuAdapter()

        task = adapter.to_research_task(
            {
                "message_id": "message-27",
                "message_type": "text",
                "content": "分析XX基金",
            }
        )

        self.assertEqual(task.message_id, "message-27")
        self.assertEqual(task.message_type, "text")
        self.assertEqual(task.user_text, "分析XX基金")

    async def test_send_and_reply_use_formatted_secret_safe_text(self) -> None:
        sender = AsyncMock(return_value={"code": 0})
        replier = AsyncMock(return_value={"code": 0})
        adapter = FeishuAdapter(send_handler=sender, reply_handler=replier)
        response = "研究完成\nAPP_SECRET=should-not-leak\nKIMI_API_KEY:secret"

        await adapter.send_message("chat-id", response)
        await adapter.reply_message("message-id", response)

        sent_text = sender.await_args.args[1]
        replied_text = replier.await_args.args[1]
        self.assertIn("研究完成", sent_text)
        self.assertIn("[敏感配置已隐藏]", sent_text)
        self.assertNotIn("should-not-leak", sent_text)
        self.assertNotIn("secret", replied_text)


class FeishuEventRoutingTests(unittest.TestCase):
    def test_routes_three_supported_research_tasks(self) -> None:
        cases = (
            ("分析XX基金", FUND_ANALYSIS),
            ("帮我生成今天的日报", DAILY_REPORT),
            ("对新能源行业做深度研究", RESEARCH_REPORT),
        )

        for message, expected in cases:
            with self.subTest(message=message):
                self.assertEqual(route_feishu_message(message), expected)

    def test_unrecognized_message_has_no_fallback_to_general_chat(self) -> None:
        self.assertIsNone(route_feishu_message("你好"))


class FeishuEventHandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        config = load_config({}, load_dotenv_file=False)
        services = initialize_services(config)
        self.assistant = AsyncMock(
            return_value=ResearchAssistantResult(
                routed_task=FUND_ANALYSIS,
                research_task_type=ResearchTaskType.FUND_RESEARCH,
                content="基金分析结果",
            )
        )
        self.services = replace(services, research_assistant=self.assistant)
        self.services = replace(
            self.services,
            feishu_adapter=FeishuAdapter(assistant_handler=self.assistant),
        )

    async def test_text_event_calls_router_and_replies_once(self) -> None:
        reply = AsyncMock(return_value={"code": 0})
        endpoint = create_feishu_event_handler(
            self.services,
            _runtime(reply_handler=reply),
        )

        response = await endpoint(
            {
                "message_id": "message-27",
                "message_type": "text",
                "content": "分析XX基金",
            }
        )

        self.assertEqual(
            response,
            {
                "code": 0,
                "status": "ok",
                "task_type": "fund_analysis",
                "result": "基金分析结果",
            },
        )
        self.assistant.assert_awaited_once()
        args, kwargs = self.assistant.await_args
        self.assertEqual(args, ("分析XX基金",))
        self.assertEqual(kwargs["routed_task"], FUND_ANALYSIS)
        reply.assert_awaited_once_with("message-27", "基金分析结果")

    async def test_unrecognized_message_returns_prompt_without_model_call(self) -> None:
        endpoint = create_feishu_event_handler(self.services, _runtime())

        response = await endpoint(
            {"message_type": "text", "content": "你好"}
        )

        self.assertEqual(response["status"], "unrecognized")
        self.assertEqual(response["message"], UNRECOGNIZED_MESSAGE)
        self.assistant.assert_not_awaited()

    async def test_unsupported_message_type_is_handled(self) -> None:
        endpoint = create_feishu_event_handler(self.services, _runtime())

        response = await endpoint(
            {"message_type": "image", "content": "image-key"}
        )

        self.assertEqual(response["status"], "unsupported")
        self.assertIn("仅支持文本", response["message"])
        self.assistant.assert_not_awaited()

    async def test_processing_exception_returns_generic_safe_error(self) -> None:
        self.assistant.side_effect = RuntimeError(
            "APP_SECRET=secret-value TABLE_ID=table-value KIMI_API_KEY=key-value"
        )
        endpoint = create_feishu_event_handler(self.services, _runtime())

        response = await endpoint(
            {"message_type": "text", "content": "分析XX基金"}
        )

        serialized = str(response)
        self.assertEqual(response["status"], "error")
        self.assertNotIn("secret-value", serialized)
        self.assertNotIn("table-value", serialized)
        self.assertNotIn("key-value", serialized)

    async def test_url_verification_returns_challenge(self) -> None:
        endpoint = create_feishu_event_handler(self.services, _runtime())

        response = await endpoint(
            {"type": "url_verification", "challenge": "challenge-27"}
        )

        self.assertEqual(response, {"challenge": "challenge-27"})


if __name__ == "__main__":
    unittest.main()
