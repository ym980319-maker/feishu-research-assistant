"""Deep research-report task handler."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.services.web_research_service import (
    KnowledgeProvider,
    PublicInfoResearcher,
    collect_research_materials,
    format_public_info,
    research_public_info,
)


ResearchReportHandler = Callable[
    [str, str, str | None, str],
    Awaitable[str],
]


async def handle_research_report(
    message: str,
    report_handler: ResearchReportHandler,
    knowledge_provider: KnowledgeProvider,
    public_info_researcher: PublicInfoResearcher = research_public_info,
) -> str:
    public_info, knowledge_text = await collect_research_materials(
        message,
        knowledge_provider,
        public_info_researcher,
    )
    return await report_handler(
        message,
        "深度报告",
        knowledge_text,
        format_public_info(public_info),
    )
