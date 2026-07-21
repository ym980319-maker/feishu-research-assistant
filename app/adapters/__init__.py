"""Deployment-facing adapters for external platforms."""

from .feishu_adapter import FeishuAdapter, ResearchTask, register_feishu_routes

__all__ = ["FeishuAdapter", "ResearchTask", "register_feishu_routes"]
