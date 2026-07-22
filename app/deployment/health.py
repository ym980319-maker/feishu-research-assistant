"""Safe production readiness and configuration checks."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from app.adapters.feishu_adapter import FeishuAdapter
from app.config import AppConfig


SERVICE_NAME = "feishu-research-assistant"


@dataclass(frozen=True, slots=True)
class ConfigurationCheck:
    status: str
    missing_required: tuple[str, ...]
    missing_optional: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.missing_required


@dataclass(frozen=True, slots=True)
class StartupCheck:
    status: str
    configuration: ConfigurationCheck
    components: Mapping[str, str]
    errors: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return self.status in {"ready", "degraded"} and not self.errors


class StartupError(RuntimeError):
    """Raised when strict production startup checks do not pass."""


def check_configuration(config: AppConfig) -> ConfigurationCheck:
    """Report names of missing settings without exposing any configured value."""
    required = {
        "FEISHU_APP_ID": config.feishu.app_id,
        "FEISHU_APP_SECRET": config.feishu.app_secret,
        "KIMI_API_KEY": config.kimi.api_key,
        "DEEPSEEK_API_KEY": config.deepseek.api_key,
    }
    optional = {
        "TAVILY_API_KEY": config.tavily.api_key,
        "FEISHU_BITABLE_APP_TOKEN": config.feishu.bitable_app_token,
        "FEISHU_NEWS_TABLE_ID": config.feishu.news_table_id,
        "FEISHU_TASK_TABLE_ID": config.feishu.task_table_id,
        "FEISHU_REPORT_TABLE_ID": config.feishu.report_table_id,
        "FEISHU_KNOWLEDGE_TABLE_ID": config.feishu.knowledge_table_id,
        "FEISHU_MARKET_TABLE_ID": config.feishu.market_table_id,
        "FEISHU_DOC_FOLDER_TOKEN": config.feishu.doc_folder_token,
    }
    if config.server.environment == "production":
        required["TAVILY_API_KEY"] = optional.pop("TAVILY_API_KEY")
    missing_required = tuple(name for name, value in required.items() if not value)
    missing_optional = tuple(name for name, value in optional.items() if not value)
    if missing_required:
        status = "error"
    elif missing_optional:
        status = "degraded"
    else:
        status = "ready"
    return ConfigurationCheck(
        status=status,
        missing_required=missing_required,
        missing_optional=missing_optional,
    )


def check_startup(
    config: AppConfig,
    *,
    public_search_provider: Any,
    feishu_adapter: FeishuAdapter,
    research_assistant: Callable[..., Any],
) -> StartupCheck:
    """Check initialized production components without making external calls."""
    configuration = check_configuration(config)
    components = {
        "public_search_provider": (
            "ready" if public_search_provider is not None else "error"
        ),
        "feishu_adapter": (
            "ready" if isinstance(feishu_adapter, FeishuAdapter) else "error"
        ),
        "research_assistant": "ready" if callable(research_assistant) else "error",
    }
    errors = tuple(
        f"组件未初始化：{name}"
        for name, status in components.items()
        if status == "error"
    )
    if errors or not configuration.ready:
        status = "error"
    elif configuration.status == "degraded":
        status = "degraded"
    else:
        status = "ready"
    return StartupCheck(
        status=status,
        configuration=configuration,
        components=components,
        errors=errors,
    )


def assert_startup_ready(check: StartupCheck) -> None:
    """Fail strict startup with explicit setting/component names only."""
    reasons = []
    if check.configuration.missing_required:
        reasons.append(
            "缺少必需配置："
            + ", ".join(check.configuration.missing_required)
        )
    reasons.extend(check.errors)
    if reasons:
        raise StartupError("应用启动检查失败：" + "；".join(reasons))


def build_health_payload(check: StartupCheck) -> dict[str, Any]:
    """Build a secret-free health response suitable for public endpoints."""
    public_status = "ok" if check.ready else "error"
    return {
        "status": public_status,
        "service": SERVICE_NAME,
        "readiness": check.status,
        "checks": {
            "configuration": {
                "status": check.configuration.status,
                "missing_required": list(check.configuration.missing_required),
                "missing_optional": list(check.configuration.missing_optional),
            },
            "components": dict(check.components),
            "errors": list(check.errors),
        },
    }


def create_health_handler(check: StartupCheck):
    async def health() -> dict[str, Any]:
        return build_health_payload(check)

    return health
