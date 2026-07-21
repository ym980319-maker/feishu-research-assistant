from __future__ import annotations

import unittest
from unittest.mock import patch

from app.adapters.feishu_adapter import FeishuAdapter
from app.bootstrap import initialize_services
from app.config import load_config
from app.deployment.health import StartupError
from app.providers.public_search_provider import MockPublicSearchProvider
from app.providers.tavily_search_provider import TavilySearchProvider


class DeploymentInitializationTests(unittest.TestCase):
    def test_initializes_provider_adapter_and_research_assistant(self) -> None:
        config = load_config({}, load_dotenv_file=False)

        services = initialize_services(config)

        self.assertIsInstance(
            services.public_search_provider,
            MockPublicSearchProvider,
        )
        self.assertIsInstance(services.feishu_adapter, FeishuAdapter)
        self.assertTrue(callable(services.research_assistant))
        self.assertEqual(services.startup_check.status, "error")

    def test_tavily_provider_is_initialized_from_loaded_config(self) -> None:
        config = load_config(
            {
                "TAVILY_API_KEY": "tavily-test-key",
                "TAVILY_SEARCH_URL": "https://search.example.com",
                "TAVILY_TIMEOUT_SECONDS": "7",
            },
            load_dotenv_file=False,
        )

        services = initialize_services(config)

        provider = services.public_search_provider
        self.assertIsInstance(provider, TavilySearchProvider)
        self.assertEqual(provider.api_key, "tavily-test-key")
        self.assertEqual(provider.endpoint, "https://search.example.com")
        self.assertEqual(provider.timeout, 7.0)

    def test_strict_startup_fails_with_clear_missing_configuration(self) -> None:
        config = load_config({}, load_dotenv_file=False)

        with self.assertRaises(StartupError) as context:
            initialize_services(config, strict_startup=True)

        message = str(context.exception)
        self.assertIn("应用启动检查失败", message)
        self.assertIn("FEISHU_APP_ID", message)
        self.assertIn("FEISHU_APP_SECRET", message)
        self.assertIn("KIMI_API_KEY", message)
        self.assertIn("DEEPSEEK_API_KEY", message)

    def test_provider_initialization_failure_has_explicit_reason(self) -> None:
        config = load_config({}, load_dotenv_file=False)

        with patch(
            "app.bootstrap.get_configured_public_search_provider",
            side_effect=RuntimeError("sensitive provider detail"),
        ), self.assertRaises(StartupError) as context:
            initialize_services(config)

        message = str(context.exception)
        self.assertIn("Public Search Provider 初始化失败", message)
        self.assertIn("RuntimeError", message)
        self.assertNotIn("sensitive provider detail", message)


if __name__ == "__main__":
    unittest.main()
