from __future__ import annotations

import asyncio
import io
import logging
from pathlib import Path
import re
import unittest
from unittest.mock import MagicMock

from app.bootstrap import initialize_services
from app.config import load_config
from app.deployment.health import StartupError, check_configuration
from app.logging_config import configure_logging
from app.server import health, run_server


ROOT = Path(__file__).resolve().parents[1]


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


def _production_environment(**overrides: str) -> dict[str, str]:
    environment = {
        "APP_ENV": "production",
        "HOST": "0.0.0.0",
        "PORT": "8000",
        "FEISHU_APP_ID": "feishu-id",
        "FEISHU_APP_SECRET": "feishu-secret",
        "KIMI_API_KEY": "kimi-key",
        "DEEPSEEK_API_KEY": "deepseek-key",
        "TAVILY_API_KEY": "tavily-key",
    }
    environment.update(overrides)
    return environment


class ProductionStartupTests(unittest.TestCase):
    def setUp(self) -> None:
        root_logger = logging.getLogger()
        self.original_handlers = list(root_logger.handlers)
        self.original_level = root_logger.level

    def tearDown(self) -> None:
        root_logger = logging.getLogger()
        root_logger.handlers.clear()
        root_logger.handlers.extend(self.original_handlers)
        root_logger.setLevel(self.original_level)

    def test_production_requires_public_provider_configuration(self) -> None:
        environment = _production_environment()
        environment.pop("TAVILY_API_KEY")
        config = load_config(environment, load_dotenv_file=False)

        check = check_configuration(config)

        self.assertEqual(check.status, "error")
        self.assertIn("TAVILY_API_KEY", check.missing_required)
        with self.assertRaises(StartupError) as context:
            initialize_services(config, strict_startup=True)
        self.assertIn("TAVILY_API_KEY", str(context.exception))

    def test_complete_production_config_starts_long_running_service(self) -> None:
        config = load_config(
            _production_environment(PORT="9200"),
            load_dotenv_file=False,
        )
        runner = MagicMock()

        run_server(
            config=config,
            application_factory=_Application,
            uvicorn_runner=runner,
        )

        runner.assert_called_once()
        _, kwargs = runner.call_args
        self.assertEqual(kwargs, {"host": "0.0.0.0", "port": 9200})

    def test_health_check_is_compatible_with_container_probe(self) -> None:
        self.assertEqual(asyncio.run(health()), {"status": "ok"})
        script = (ROOT / "scripts" / "health_check.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("/health", script)
        self.assertIn('{"status": "ok"}', script)

    def test_logging_has_required_fields_and_redacts_secrets(self) -> None:
        stream = io.StringIO()
        configure_logging("INFO", stream=stream)
        logger = logging.getLogger("app.production")

        logger.info(
            "启动检查 APP_SECRET=%s KIMI_API_KEY=%s Authorization: Bearer %s",
            "secret-value",
            "model-key-value",
            "bearer-value",
        )
        output = stream.getvalue()

        self.assertRegex(
            output,
            re.compile(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}.*"
                r" \| app\.production \| INFO \| "
            ),
        )
        self.assertIn("[敏感配置已隐藏]", output)
        self.assertNotIn("secret-value", output)
        self.assertNotIn("model-key-value", output)
        self.assertNotIn("bearer-value", output)

    def test_deployment_artifacts_start_standalone_server(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        start_script = (ROOT / "scripts" / "start.sh").read_text(
            encoding="utf-8"
        )
        attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")

        self.assertIn("USER app", dockerfile)
        self.assertIn("HEALTHCHECK", dockerfile)
        self.assertIn("restart: unless-stopped", compose)
        self.assertIn("exec python -m app.server", start_script)
        self.assertIn("*.sh text eol=lf", attributes)


if __name__ == "__main__":
    unittest.main()
