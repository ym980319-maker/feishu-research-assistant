from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from app.bootstrap import initialize_services
from app.config import load_config
from app.deployment.check import check_environment
from app.server import RuntimeDependencies, create_server_app


ROOT = Path(__file__).resolve().parents[1]


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


def _environment(**overrides: str) -> dict[str, str]:
    environment = {
        "APP_ENV": "production",
        "FEISHU_APP_ID": "feishu-id",
        "FEISHU_APP_SECRET": "feishu-secret",
        "KIMI_API_KEY": "kimi-key",
        "DEEPSEEK_API_KEY": "deepseek-key",
        "TAVILY_API_KEY": "tavily-key",
    }
    environment.update(overrides)
    return environment


def _runtime(*, reply_handler=None) -> RuntimeDependencies:
    return RuntimeDependencies(
        kimi_handler=AsyncMock(return_value="基金投资决策联调结果"),
        deepseek_handler=AsyncMock(return_value="普通问答结果"),
        knowledge_provider=AsyncMock(return_value="内部基金研究材料"),
        deep_report_handler=AsyncMock(return_value="深度研究联调结果"),
        legacy_daily_handler=AsyncMock(return_value="兼容日报结果"),
        reply_message_handler=reply_handler,
    )


class ProductionEnvironmentCheckTests(unittest.TestCase):
    def test_complete_environment_passes_without_returning_values(self) -> None:
        config = load_config(_environment(), load_dotenv_file=False)

        result = check_environment(config)

        self.assertTrue(result.ready)
        self.assertEqual(result.missing, ())
        self.assertNotIn("kimi-key", result.message)
        self.assertNotIn("feishu-secret", result.message)

    def test_missing_environment_reports_names_only(self) -> None:
        environment = _environment()
        environment.pop("TAVILY_API_KEY")
        environment.pop("FEISHU_APP_SECRET")
        config = load_config(environment, load_dotenv_file=False)

        result = check_environment(config)

        self.assertFalse(result.ready)
        self.assertEqual(
            result.missing,
            ("FEISHU_APP_SECRET", "TAVILY_API_KEY"),
        )
        self.assertIn("FEISHU_APP_SECRET", result.message)
        self.assertIn("TAVILY_API_KEY", result.message)
        self.assertNotIn("feishu-id", result.message)

    def test_start_script_runs_environment_check_before_server(self) -> None:
        script = (ROOT / "scripts" / "start.sh").read_text(encoding="utf-8")

        check_position = script.index("python -m app.deployment.check")
        server_position = script.index("exec python -m app.server")
        self.assertLess(check_position, server_position)


class FeishuIntegrationFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        config = load_config(_environment(), load_dotenv_file=False)
        self.public_search = AsyncMock(
            return_value={
                "title": "公开信息联调样本",
                "source": "测试公开来源",
                "url": "https://example.test/research",
                "publish_time": "2026-07-22",
                "content": "仅用于验证 Evidence 调用链，不发起真实网络请求。",
            }
        )
        self.services = replace(
            initialize_services(config),
            public_search_provider=SimpleNamespace(search=self.public_search),
        )
        self.reply = AsyncMock(return_value={"code": 0})
        self.runtime = _runtime(reply_handler=self.reply)
        self.application = create_server_app(
            config=config,
            services=self.services,
            runtime=self.runtime,
            application_factory=_Application,
        )

    async def test_feishu_fund_message_runs_router_and_returns_result(self) -> None:
        response = await self.application.endpoints["/feishu/events"](
            {
                "event": {
                    "message": {
                        "message_id": "integration-message-29",
                        "message_type": "text",
                        "content": json.dumps(
                            {"text": "请分析XX基金并生成投资决策意见"},
                            ensure_ascii=False,
                        ),
                    }
                }
            }
        )

        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["task_type"], "fund_analysis")
        self.assertEqual(response["result"], "基金投资决策联调结果")
        self.runtime.kimi_handler.assert_awaited_once()
        self.runtime.deepseek_handler.assert_not_awaited()
        self.runtime.deep_report_handler.assert_not_awaited()
        self.assertEqual(self.public_search.await_count, 4)
        self.reply.assert_awaited_once_with(
            "integration-message-29",
            "基金投资决策联调结果",
        )

    async def test_feishu_deep_research_uses_existing_report_chain(self) -> None:
        response = await self.application.endpoints["/feishu/events"](
            {
                "message_id": "deep-message-29",
                "message_type": "text",
                "content": "对新能源行业做深度研究",
            }
        )

        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["task_type"], "research_report")
        self.assertEqual(response["result"], "深度研究联调结果")
        self.runtime.deep_report_handler.assert_awaited_once()
        self.runtime.kimi_handler.assert_not_awaited()
        self.public_search.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
