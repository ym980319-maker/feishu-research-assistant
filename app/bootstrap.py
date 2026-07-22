"""Application assembly independent from the deployment entry module."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.adapters.feishu_adapter import FeishuAdapter, register_feishu_routes
from app.config import AppConfig, load_config
from app.deployment.health import (
    StartupCheck,
    StartupError,
    assert_startup_ready,
    check_startup,
    create_health_handler,
)
from app.providers.public_search_provider import (
    PublicSearchProvider,
    get_configured_public_search_provider,
)
from app.services.research_assistant_service import handle_research_assistant


EventHandler = Callable[[Any], Awaitable[Any]]
HealthHandler = Callable[[], Awaitable[Any]]
ApplicationFactory = Callable[..., Any]
ResearchAssistantHandler = Callable[..., Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class ApplicationServices:
    config: AppConfig
    public_search_provider: PublicSearchProvider
    research_assistant: ResearchAssistantHandler
    feishu_adapter: FeishuAdapter
    startup_check: StartupCheck


def initialize_services(
    config: AppConfig | None = None,
    *,
    strict_startup: bool = False,
) -> ApplicationServices:
    """Load configuration and initialize deployment-facing service objects."""
    selected_config = config or load_config()
    research_assistant = handle_research_assistant
    try:
        public_search_provider = get_configured_public_search_provider(
            selected_config.tavily
        )
    except Exception as exc:
        raise StartupError(
            "应用启动失败：Public Search Provider 初始化失败（"
            f"{type(exc).__name__}）"
        ) from exc
    feishu_adapter = FeishuAdapter(assistant_handler=research_assistant)
    startup_check = check_startup(
        selected_config,
        public_search_provider=public_search_provider,
        feishu_adapter=feishu_adapter,
        research_assistant=research_assistant,
    )
    if strict_startup:
        assert_startup_ready(startup_check)
    return ApplicationServices(
        config=selected_config,
        public_search_provider=public_search_provider,
        research_assistant=research_assistant,
        feishu_adapter=feishu_adapter,
        startup_check=startup_check,
    )


def create_app(
    *,
    config: AppConfig | None = None,
    services: ApplicationServices | None = None,
    feishu_event_handler: EventHandler | None = None,
    application_factory: ApplicationFactory | None = None,
    strict_startup: bool = False,
    health_handler: HealthHandler | None = None,
) -> Any:
    """Build the ASGI app, initialize services and register transport routes."""
    selected_services = services or initialize_services(
        config,
        strict_startup=strict_startup,
    )
    if application_factory is None:
        from fastapi import FastAPI

        application_factory = FastAPI
    application = application_factory(title="Feishu Research Assistant")
    selected_health_handler = health_handler or create_health_handler(
        selected_services.startup_check
    )
    application.get("/health")(selected_health_handler)
    if feishu_event_handler is not None:
        register_feishu_routes(application, feishu_event_handler)

    # A simple attribute works both in FastAPI and lightweight deployment
    # shims, while keeping construction observable in tests and future hosts.
    application.research_services = selected_services
    return application
