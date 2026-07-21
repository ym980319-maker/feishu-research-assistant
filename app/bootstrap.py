"""Application assembly independent from the deployment entry module."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.adapters.feishu_adapter import FeishuAdapter, register_feishu_routes
from app.config import AppConfig, load_config


EventHandler = Callable[[Any], Awaitable[Any]]
ApplicationFactory = Callable[..., Any]


@dataclass(frozen=True, slots=True)
class ApplicationServices:
    config: AppConfig
    feishu_adapter: FeishuAdapter


def initialize_services(config: AppConfig | None = None) -> ApplicationServices:
    """Load configuration and initialize deployment-facing service objects."""
    selected_config = config or load_config()
    return ApplicationServices(
        config=selected_config,
        feishu_adapter=FeishuAdapter(),
    )


async def health() -> dict[str, str]:
    return {"status": "ok", "service": "feishu-research-assistant"}


def create_app(
    *,
    config: AppConfig | None = None,
    services: ApplicationServices | None = None,
    feishu_event_handler: EventHandler | None = None,
    application_factory: ApplicationFactory | None = None,
) -> Any:
    """Build the ASGI app, initialize services and register transport routes."""
    selected_services = services or initialize_services(config)
    if application_factory is None:
        from fastapi import FastAPI

        application_factory = FastAPI
    application = application_factory(title="Feishu Research Assistant")
    application.get("/health")(health)
    if feishu_event_handler is not None:
        register_feishu_routes(application, feishu_event_handler)

    # A simple attribute works both in FastAPI and lightweight deployment
    # shims, while keeping construction observable in tests and future hosts.
    application.research_services = selected_services
    return application
