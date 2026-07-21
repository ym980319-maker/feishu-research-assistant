"""Deep research-report task handler."""

from __future__ import annotations

from collections.abc import Awaitable, Callable


ResearchReportHandler = Callable[[str, str], Awaitable[str]]


async def handle_research_report(
    message: str,
    report_handler: ResearchReportHandler,
) -> str:
    return await report_handler(message, "深度报告")

