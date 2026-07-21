"""Fund product research task handler."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.services.evidence_service import (
    EvidenceResearcher,
    KnowledgeProvider,
)
from app.services.fund_investment_decision_service import (
    FundDocumentInput,
    generate_fund_investment_decision,
)


ModelHandler = Callable[[str, str], Awaitable[str]]


async def handle_fund_analysis(
    message: str,
    model_handler: ModelHandler,
    knowledge_provider: KnowledgeProvider,
    public_info_researcher: EvidenceResearcher | None = None,
    documents: FundDocumentInput = None,
) -> str:
    return await generate_fund_investment_decision(
        message,
        model_handler,
        knowledge_provider,
        documents=documents,
        evidence_researcher=public_info_researcher,
    )
