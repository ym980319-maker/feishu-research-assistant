from __future__ import annotations

import unittest
from pathlib import Path

from app.config import (
    DEFAULT_DEEPSEEK_BASE_URL,
    DEFAULT_FEISHU_TENANT_DOMAIN,
    DEFAULT_KIMI_BASE_URL,
    DEFAULT_KIMI_MODEL,
    DEFAULT_SERVER_HOST,
    DEFAULT_SERVER_PORT,
    DEFAULT_TAVILY_ENDPOINT,
    load_config,
)


class ConfigTests(unittest.TestCase):
    def test_loads_all_external_system_configuration(self) -> None:
        config = load_config(
            {
                "FEISHU_APP_ID": "feishu-id",
                "FEISHU_APP_SECRET": "feishu-secret",
                "FEISHU_BITABLE_APP_TOKEN": "bitable-token",
                "FEISHU_NEWS_TABLE_ID": "news-table",
                "FEISHU_TASK_TABLE_ID": "task-table",
                "FEISHU_REPORT_TABLE_ID": "report-table",
                "FEISHU_KNOWLEDGE_TABLE_ID": "knowledge-table",
                "FEISHU_MARKET_TABLE_ID": "market-table",
                "FEISHU_DOC_FOLDER_TOKEN": "folder-token",
                "FEISHU_TENANT_DOMAIN": "tenant.feishu.cn",
                "KIMI_API_KEY": "kimi-key",
                "KIMI_BASE_URL": "https://kimi.example.com",
                "KIMI_MODEL": "kimi-test",
                "DEEPSEEK_API_KEY": "deepseek-key",
                "DEEPSEEK_BASE_URL": "https://deepseek.example.com",
                "TAVILY_API_KEY": "tavily-key",
                "TAVILY_SEARCH_URL": "https://tavily.example.com/search",
                "TAVILY_TIMEOUT_SECONDS": "8.5",
                "HOST": "127.0.0.1",
                "PORT": "9000",
                "APP_ENV": "production",
            },
            load_dotenv_file=False,
        )

        self.assertEqual(config.feishu.app_id, "feishu-id")
        self.assertEqual(config.feishu.app_secret, "feishu-secret")
        self.assertEqual(config.feishu.bitable_app_token, "bitable-token")
        self.assertEqual(config.feishu.news_table_id, "news-table")
        self.assertEqual(config.feishu.task_table_id, "task-table")
        self.assertEqual(config.feishu.report_table_id, "report-table")
        self.assertEqual(config.feishu.knowledge_table_id, "knowledge-table")
        self.assertEqual(config.feishu.market_table_id, "market-table")
        self.assertEqual(config.feishu.doc_folder_token, "folder-token")
        self.assertEqual(config.feishu.tenant_domain, "tenant.feishu.cn")
        self.assertEqual(config.kimi.api_key, "kimi-key")
        self.assertEqual(config.kimi.base_url, "https://kimi.example.com")
        self.assertEqual(config.kimi.model, "kimi-test")
        self.assertEqual(config.deepseek.api_key, "deepseek-key")
        self.assertEqual(config.deepseek.base_url, "https://deepseek.example.com")
        self.assertEqual(config.tavily.api_key, "tavily-key")
        self.assertEqual(config.tavily.endpoint, "https://tavily.example.com/search")
        self.assertEqual(config.tavily.timeout_seconds, 8.5)
        self.assertEqual(config.server.host, "127.0.0.1")
        self.assertEqual(config.server.port, 9000)
        self.assertEqual(config.server.environment, "production")

    def test_uses_stable_defaults_without_environment_values(self) -> None:
        config = load_config({}, load_dotenv_file=False)

        self.assertEqual(config.feishu.tenant_domain, DEFAULT_FEISHU_TENANT_DOMAIN)
        self.assertEqual(config.kimi.base_url, DEFAULT_KIMI_BASE_URL)
        self.assertEqual(config.kimi.model, DEFAULT_KIMI_MODEL)
        self.assertEqual(config.deepseek.base_url, DEFAULT_DEEPSEEK_BASE_URL)
        self.assertEqual(config.tavily.endpoint, DEFAULT_TAVILY_ENDPOINT)
        self.assertEqual(config.server.host, DEFAULT_SERVER_HOST)
        self.assertEqual(config.server.port, DEFAULT_SERVER_PORT)
        self.assertEqual(config.server.environment, "development")

    def test_invalid_tavily_timeout_uses_default(self) -> None:
        config = load_config(
            {"TAVILY_TIMEOUT_SECONDS": "invalid"},
            load_dotenv_file=False,
        )

        self.assertEqual(config.tavily.timeout_seconds, 20.0)

    def test_business_services_do_not_read_environment_directly(self) -> None:
        services_dir = Path(__file__).resolve().parents[1] / "app" / "services"
        for path in services_dir.glob("*.py"):
            content = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertNotIn("os.getenv", content)
                self.assertNotIn("os.environ", content)


if __name__ == "__main__":
    unittest.main()
