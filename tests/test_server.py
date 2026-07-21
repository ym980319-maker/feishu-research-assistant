from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

from pydantic import ValidationError

from app.bootstrap import initialize_services
from app.config import load_config
from app.models.request import ResearchRequest
from app.models.research_task import ResearchTaskType
from app.router.task_router import FUND_ANALYSIS
from app.server import (
    ResearchRequestError,
    RuntimeDependencies,
    create_server_app,
    run_server,
)
from app.services.research_assistant_service import ResearchAssistantResult


class _Route:
    def __init__(self, path: str):
        self.path = path


class _Application:
    def __init__(self, **kwargs):
        self.title = kwargs.get("title")
        self.routes = []
        self.endpoints = {}

    def get(self, path: str):
        return self._register(path)

    def post(self, path: str):
        return self._register(path)

    def _register(self, path: str):
        def decorator(function):
            self.routes.append(_Route(path))
            self.endpoints[path] = function
            return function

        return decorator


def _runtime() -> RuntimeDependencies:
    return RuntimeDependencies(
        kimi_handler=AsyncMock(return_value="Kimi 结果"),
        deepseek_handler=AsyncMock(return_value="DeepSeek 结果"),
        knowledge_provider=AsyncMock(return_value="知识库"),
        deep_report_handler=AsyncMock(return_value="深度报告"),
        legacy_daily_handler=AsyncMock(return_value="日报"),
    )


def _required_config(**extra: str):
    environment = {
        "FEISHU_APP_ID": "feishu-id",
        "FEISHU_APP_SECRET": "feishu-secret",
        "KIMI_API_KEY": "kimi-key",
        "DEEPSEEK_API_KEY": "deepseek-key",
    }
    environment.update(extra)
    return load_config(environment, load_dotenv_file=False)


class ServerEndpointTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        config = _required_config()
        services = initialize_services(config)
        self.assistant = AsyncMock(
            return_value=ResearchAssistantResult(
                routed_task=FUND_ANALYSIS,
                research_task_type=ResearchTaskType.FUND_RESEARCH,
                content="基金投资决策结果",
            )
        )
        self.services = replace(
            services,
            research_assistant=self.assistant,
        )
        self.application = create_server_app(
            config=config,
            services=self.services,
            runtime=_runtime(),
            application_factory=_Application,
        )

    async def test_health_endpoint_returns_minimal_status(self) -> None:
        response = await self.application.endpoints["/health"]()

        self.assertEqual(response, {"status": "ok"})

    async def test_research_endpoint_calls_research_assistant_router(self) -> None:
        response = await self.application.endpoints["/research"](
            ResearchRequest(
                task_type="fund_analysis",
                query="分析这只基金",
            )
        )

        self.assertEqual(response.status, "ok")
        self.assertEqual(response.task_type, FUND_ANALYSIS)
        self.assertEqual(response.query, "分析这只基金")
        self.assertEqual(response.research_task_type, "基金研究")
        self.assertEqual(response.content, "基金投资决策结果")
        self.assistant.assert_awaited_once()
        args, kwargs = self.assistant.await_args
        self.assertEqual(args, ("分析这只基金",))
        self.assertEqual(kwargs["routed_task"], FUND_ANALYSIS)
        self.assertTrue(callable(kwargs["evidence_researcher"]))

    async def test_unsupported_task_type_is_rejected(self) -> None:
        with self.assertRaises(ResearchRequestError) as context:
            await self.application.endpoints["/research"](
                ResearchRequest(
                    task_type="unsupported",
                    query="测试",
                )
            )

        self.assertIn("不支持的 task_type", str(context.exception))
        self.assistant.assert_not_awaited()

    def test_empty_or_extra_request_fields_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            ResearchRequest(task_type="", query="")
        with self.assertRaises(ValidationError):
            ResearchRequest(
                task_type="fund_analysis",
                query="测试",
                unexpected="value",
            )


class ServerStartupTests(unittest.TestCase):
    def test_run_server_uses_bootstrap_and_configured_host_port(self) -> None:
        config = _required_config(HOST="127.0.0.1", PORT="9100")
        runner = MagicMock()

        run_server(
            config=config,
            application_factory=_Application,
            uvicorn_runner=runner,
        )

        runner.assert_called_once()
        args, kwargs = runner.call_args
        self.assertIsInstance(args[0], _Application)
        self.assertEqual(kwargs, {"host": "127.0.0.1", "port": 9100})


if __name__ == "__main__":
    unittest.main()
