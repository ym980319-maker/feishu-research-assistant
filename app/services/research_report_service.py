"""Deep research-report task handler."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.services.evidence_service import (
    EvidenceResearcher,
    collect_evidence_materials,
    format_evidence_pool,
)
from app.services.web_research_service import (
    KnowledgeProvider,
)


ResearchReportHandler = Callable[
    [str, str, str | None, str],
    Awaitable[str],
]


async def handle_research_report(
    message: str,
    report_handler: ResearchReportHandler,
    knowledge_provider: KnowledgeProvider,
    public_info_researcher: EvidenceResearcher | None = None,
) -> str:
    evidence_pool, knowledge_text = await collect_evidence_materials(
        message,
        knowledge_provider,
        public_info_researcher,
    )
    return await report_handler(
        message,
        "深度报告",
        knowledge_text,
        format_evidence_pool(evidence_pool),
    )
