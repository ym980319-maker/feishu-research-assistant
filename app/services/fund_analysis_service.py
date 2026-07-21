"""Fund product research task handler."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.services.web_research_service import (
    KnowledgeProvider,
    PublicInfoResearcher,
    build_research_prompt,
    collect_research_materials,
    research_public_info,
)


ModelHandler = Callable[[str, str], Awaitable[str]]


async def handle_fund_analysis(
    message: str,
    model_handler: ModelHandler,
    knowledge_provider: KnowledgeProvider,
    public_info_researcher: PublicInfoResearcher = research_public_info,
) -> str:
    public_info, knowledge_text = await collect_research_materials(
        message,
        knowledge_provider,
        public_info_researcher,
    )
    prompt = build_research_prompt(message, public_info, knowledge_text)
    return await model_handler(prompt, "基金产品研究")
