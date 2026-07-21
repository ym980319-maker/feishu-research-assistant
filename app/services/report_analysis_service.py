"""Research-report analysis task handler."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.services.evidence_service import (
    EvidenceResearcher,
    build_evidence_research_prompt,
    collect_evidence_materials,
)
from app.services.web_research_service import (
    KnowledgeProvider,
    public_information_requested,
)


ModelHandler = Callable[[str, str], Awaitable[str]]


async def handle_report_analysis(
    message: str,
    model_handler: ModelHandler,
    knowledge_provider: KnowledgeProvider,
    public_info_researcher: EvidenceResearcher | None = None,
    *,
    include_public_info: bool | None = None,
) -> str:
    if include_public_info is None:
        include_public_info = public_information_requested(message)
    evidence_pool, knowledge_text = await collect_evidence_materials(
        message,
        knowledge_provider,
        public_info_researcher,
        include_public_info=include_public_info,
    )
    prompt = build_evidence_research_prompt(
        message,
        evidence_pool,
        knowledge_text,
    )
    return await model_handler(prompt, "研报摘要")
