from __future__ import annotations

import json
import unittest

from app.adapters.feishu_adapter import FeishuAdapter
from app.config import load_config
from app.deployment.health import (
    assert_startup_ready,
    build_health_payload,
    check_configuration,
    check_startup,
    create_health_handler,
)


def _configured_environment() -> dict[str, str]:
    return {
        "FEISHU_APP_ID": "feishu-app-sensitive",
        "FEISHU_APP_SECRET": "feishu-secret-sensitive",
        "FEISHU_BITABLE_APP_TOKEN": "bitable-sensitive",
        "FEISHU_NEWS_TABLE_ID": "news-table",
        "FEISHU_TASK_TABLE_ID": "task-table",
        "FEISHU_REPORT_TABLE_ID": "report-table",
        "FEISHU_KNOWLEDGE_TABLE_ID": "knowledge-table",
        "FEISHU_MARKET_TABLE_ID": "market-table",
        "FEISHU_DOC_FOLDER_TOKEN": "folder-sensitive",
        "KIMI_API_KEY": "kimi-sensitive",
        "DEEPSEEK_API_KEY": "deepseek-sensitive",
        "TAVILY_API_KEY": "tavily-sensitive",
    }


class HealthCheckTests(unittest.IsolatedAsyncioTestCase):
    def test_complete_configuration_is_ready(self) -> None:
        config = load_config(
            _configured_environment(),
            load_dotenv_file=False,
        )

        check = check_configuration(config)

        self.assertTrue(check.ready)
        self.assertEqual(check.status, "ready")
        self.assertEqual(check.missing_required, ())
        self.assertEqual(check.missing_optional, ())

    def test_missing_configuration_reports_names_only(self) -> None:
        config = load_config({}, load_dotenv_file=False)

        check = check_configuration(config)

        self.assertFalse(check.ready)
        self.assertEqual(check.status, "error")
        self.assertIn("FEISHU_APP_ID", check.missing_required)
        self.assertIn("KIMI_API_KEY", check.missing_required)
        self.assertIn("TAVILY_API_KEY", check.missing_optional)

    async def test_health_payload_never_exposes_configuration_values(self) -> None:
        environment = _configured_environment()
        config = load_config(environment, load_dotenv_file=False)
        startup = check_startup(
            config,
            public_search_provider=object(),
            feishu_adapter=FeishuAdapter(),
            research_assistant=lambda *args, **kwargs: None,
        )

        payload = await create_health_handler(startup)()
        serialized = json.dumps(payload, ensure_ascii=False)

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["readiness"], "ready")
        for secret in environment.values():
            self.assertNotIn(secret, serialized)

    def test_component_failure_is_visible_without_exception_details(self) -> None:
        config = load_config(
            _configured_environment(),
            load_dotenv_file=False,
        )

        startup = check_startup(
            config,
            public_search_provider=None,
            feishu_adapter=FeishuAdapter(),
            research_assistant=lambda *args, **kwargs: None,
        )
        payload = build_health_payload(startup)

        self.assertEqual(payload["status"], "error")
        self.assertEqual(
            payload["checks"]["components"]["public_search_provider"],
            "error",
        )
        self.assertIn("组件未初始化：public_search_provider", payload["checks"]["errors"])

    def test_optional_configuration_allows_degraded_startup(self) -> None:
        config = load_config(
            {
                "FEISHU_APP_ID": "id",
                "FEISHU_APP_SECRET": "secret",
                "KIMI_API_KEY": "kimi",
                "DEEPSEEK_API_KEY": "deepseek",
            },
            load_dotenv_file=False,
        )
        startup = check_startup(
            config,
            public_search_provider=object(),
            feishu_adapter=FeishuAdapter(),
            research_assistant=lambda *args, **kwargs: None,
        )

        self.assertEqual(startup.status, "degraded")
        self.assertTrue(startup.ready)
        assert_startup_ready(startup)


if __name__ == "__main__":
    unittest.main()
