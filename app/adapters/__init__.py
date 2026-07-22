"""Deployment-facing adapters for external platforms."""

from .feishu_adapter import FeishuAdapter, ResearchTask, register_feishu_routes
from .feishu_event_handler import (
    FeishuEventHandler,
    create_feishu_event_handler,
    route_feishu_message,
)

__all__ = [
    "FeishuAdapter",
    "FeishuEventHandler",
    "ResearchTask",
    "create_feishu_event_handler",
    "register_feishu_routes",
    "route_feishu_message",
]
