"""Unified orchestration entry for the professional research assistant."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.models.research_task import ResearchTaskType
from app.router.task_router import (
    DAILY_REPORT,
    FUND_ANALYSIS,
    GENERAL_CHAT,
    REPORT_ANALYSIS,
    RESEARCH_REPORT,
    SENTIMENT_ANALYSIS,
    route_task,
)
from app.services.daily_report_service import handle_daily_report
from app.services.evidence_service import (
    EvidenceResearcher,
    KnowledgeProvider,
    build_evidence_research_prompt,
    collect_evidence_materials,
)
from app.services.fund_analysis_service import handle_fund_analysis
from app.services.fund_investment_decision_service import FundDocumentInput
from app.services.general_chat_service import handle_general_chat
from app.services.report_analysis_service import handle_report_analysis
from app.services.research_report_service import handle_research_report


ModelHandler = Callable[[str, str], Awaitable[str]]
DeepReportHandler = Callable[[str, str, str | None, str], Awaitable[str]]
DailyReportHandler = Callable[[str], Awaitable[str]]

MACRO_MARKERS = (
    "宏观",
    "经济",
    "通胀",
    "货币政策",
    "财政政策",
    "利率",
    "汇率",
)
INDUSTRY_MARKERS = (
    "行业",
    "产业",
    "产业链",
    "赛道",
    "供需",
)


@dataclass(frozen=True, slots=True)
class ResearchAssistantResult:
    routed_task: str
    research_task_type: ResearchTaskType | None
    content: str


def identify_research_task_type(
    message: str,
    routed_task: str | None = None,
) -> ResearchTaskType | None:
    task = routed_task or route_task(message)
    if task == SENTIMENT_ANALYSIS:
        return ResearchTaskType.SENTIMENT_RESEARCH
    if task == FUND_ANALYSIS:
        return ResearchTaskType.FUND_RESEARCH

    normalized = str(message or "")
    if any(marker in normalized for marker in MACRO_MARKERS):
        return ResearchTaskType.MACRO_RESEARCH
    if any(marker in normalized for marker in INDUSTRY_MARKERS):
        return ResearchTaskType.INDUSTRY_RESEARCH
    if task in (REPORT_ANALYSIS, RESEARCH_REPORT):
        return ResearchTaskType.COMPANY_RESEARCH
    return None


async def _handle_sentiment_research(
    message: str,
    kimi_handler: ModelHandler,
    knowledge_provider: KnowledgeProvider,
    evidence_researcher: EvidenceResearcher | None,
) -> str:
    evidence_pool, knowledge_text = await collect_evidence_materials(
        message,
        knowledge_provider,
        evidence_researcher,
        include_public_info=True,
    )
    prompt = build_evidence_research_prompt(
        message,
        evidence_pool,
        knowledge_text,
    )
    prompt += """

请输出专业舆情研究，至少包括：
一、最新公开信息
二、监管动态
三、市场关注点
四、影响判断与风险提示

所有互联网事实必须对应 Evidence Pool 中的来源；无来源信息不得写成事实。
"""
    return await kimi_handler(prompt, "舆情梳理")


async def handle_research_assistant(
    message: str,
    *,
    kimi_handler: ModelHandler,
    deepseek_handler: ModelHandler,
    knowledge_provider: KnowledgeProvider,
    deep_report_handler: DeepReportHandler,
    legacy_daily_handler: DailyReportHandler | None = None,
    fund_documents: FundDocumentInput = None,
    evidence_researcher: EvidenceResearcher | None = None,
    routed_task: str | None = None,
) -> ResearchAssistantResult:
    """Route one user request through the appropriate research workflow."""
    task = routed_task or route_task(message)
    research_type = identify_research_task_type(message, task)

    if task == SENTIMENT_ANALYSIS:
        content = await _handle_sentiment_research(
            message,
            kimi_handler,
            knowledge_provider,
            evidence_researcher,
        )
    elif task == REPORT_ANALYSIS:
        content = await handle_report_analysis(
            message,
            kimi_handler,
            knowledge_provider,
            evidence_researcher,
            include_public_info=True,
        )
    elif task == RESEARCH_REPORT:
        content = await handle_research_report(
            message,
            deep_report_handler,
            knowledge_provider,
            evidence_researcher,
        )
    elif task == FUND_ANALYSIS:
        content = await handle_fund_analysis(
            message,
            kimi_handler,
            knowledge_provider,
            evidence_researcher,
            documents=fund_documents,
        )
    elif task == DAILY_REPORT and legacy_daily_handler is not None:
        # Legacy compatibility only; daily reports are no longer a primary
        # research-assistant route in the message entry.
        content = await handle_daily_report(message, legacy_daily_handler)
    else:
        task = GENERAL_CHAT
        content = await handle_general_chat(message, deepseek_handler)

    return ResearchAssistantResult(
        routed_task=task,
        research_task_type=research_type,
        content=content,
    )
