from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock

from app.adapters.feishu_adapter import FeishuAdapter, ResearchTask
from app.bootstrap import create_app, initialize_services
from app.config import load_config
from app.models.research_task import ResearchTaskType
from app.router.task_router import REPORT_ANALYSIS
from app.services.research_assistant_service import ResearchAssistantResult


class _Route:
    def __init__(self, path: str):
        self.path = path


class _Application:
    def __init__(self, **kwargs):
        self.title = kwargs.get("title")
        self.routes = []

    def get(self, path: str):
        return self._register(path)

    def post(self, path: str):
        return self._register(path)

    def _register(self, path: str):
        def decorator(function):
            self.routes.append(_Route(path))
            return function

        return decorator


class BootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config({}, load_dotenv_file=False)

    def test_initializes_services_with_supplied_configuration(self) -> None:
        services = initialize_services(self.config)

        self.assertIs(services.config, self.config)
        self.assertIsInstance(services.feishu_adapter, FeishuAdapter)

    def test_create_app_registers_health_and_feishu_routes(self) -> None:
        async def event_handler(request):
            return {"code": 0}

        application = create_app(
            config=self.config,
            feishu_event_handler=event_handler,
            application_factory=_Application,
        )
        paths = {route.path for route in application.routes}

        self.assertIn("/health", paths)
        self.assertIn("/feishu/events", paths)
        self.assertIs(application.research_services.config, self.config)

    def test_feishu_adapter_converts_event_to_internal_task(self) -> None:
        adapter = FeishuAdapter()
        payload = {
            "event": {
                "message": {
                    "message_id": "message-24",
                    "message_type": "text",
                    "content": json.dumps(
                        {"text": "@机器人 分析这份研报"},
                        ensure_ascii=False,
                    ),
                }
            }
        }

        task = adapter.to_research_task(
            payload,
            lambda value: value.replace("@机器人", "").strip(),
        )

        self.assertEqual(task.message_id, "message-24")
        self.assertEqual(task.message_type, "text")
        self.assertEqual(task.user_text, "分析这份研报")
        self.assertEqual(task.raw_message["message_id"], "message-24")


class FeishuAdapterDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_dispatch_calls_unified_research_assistant(self) -> None:
        expected = ResearchAssistantResult(
            routed_task=REPORT_ANALYSIS,
            research_task_type=ResearchTaskType.COMPANY_RESEARCH,
            content="研报解析结果",
        )
        assistant = AsyncMock(return_value=expected)
        adapter = FeishuAdapter(assistant_handler=assistant)
        task = ResearchTask(
            message_id="message-24",
            message_type="text",
            user_text="分析这份研报",
            raw_message={},
        )
        kimi = AsyncMock()
        deepseek = AsyncMock()
        knowledge = AsyncMock()
        deep_report = AsyncMock()

        result = await adapter.dispatch(
            task,
            kimi_handler=kimi,
            deepseek_handler=deepseek,
            knowledge_provider=knowledge,
            deep_report_handler=deep_report,
        )

        self.assertIs(result, expected)
        assistant.assert_awaited_once()
        args, kwargs = assistant.await_args
        self.assertEqual(args, ("分析这份研报",))
        self.assertEqual(kwargs["routed_task"], REPORT_ANALYSIS)
        self.assertIs(kwargs["kimi_handler"], kimi)
        self.assertIs(kwargs["deepseek_handler"], deepseek)


if __name__ == "__main__":
    unittest.main()
