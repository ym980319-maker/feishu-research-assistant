"""Research-report analysis task handler."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.services.web_research_service import (
    KnowledgeProvider,
    PublicInfoResearcher,
    build_research_prompt,
    collect_research_materials,
    public_information_requested,
    research_public_info,
)


ModelHandler = Callable[[str, str], Awaitable[str]]


async def handle_report_analysis(
    message: str,
    model_handler: ModelHandler,
    knowledge_provider: KnowledgeProvider,
    public_info_researcher: PublicInfoResearcher = research_public_info,
) -> str:
    public_info, knowledge_text = await collect_research_materials(
        message,
        knowledge_provider,
        public_info_researcher,
        include_public_info=public_information_requested(message),
    )
    prompt = build_research_prompt(message, public_info, knowledge_text)
    return await model_handler(prompt, "研报摘要")
