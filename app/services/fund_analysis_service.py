"""Fund product research task handler."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.services.evidence_service import (
    EvidenceResearcher,
    KnowledgeProvider,
)
from app.services.fund_document_service import FundDocumentService
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
    structured_documents = documents
    if documents is not None:
        structured_json = FundDocumentService().extract_json(documents)
        if isinstance(documents, str):
            raw_documents = [documents]
        else:
            raw_documents = [
                str(document or "").strip()
                for document in documents
                if str(document or "").strip()
            ]
        structured_documents = [
            f"【结构化字段提取】\n{structured_json}",
            *(
                f"【原始文件正文 {index}】\n{document}"
                for index, document in enumerate(raw_documents, start=1)
            ),
        ]
    return await generate_fund_investment_decision(
        message,
        model_handler,
        knowledge_provider,
        documents=structured_documents,
        evidence_researcher=public_info_researcher,
    )
