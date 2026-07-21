"""Classify user messages into research-assistant task types."""

from __future__ import annotations

import re


SENTIMENT_ANALYSIS = "sentiment_analysis"
REPORT_ANALYSIS = "report_analysis"
DEEP_RESEARCH = "deep_research"
FUND_INVESTMENT_DECISION = "fund_investment_decision"
# Backward-compatible names used by existing imports and tests.
RESEARCH_REPORT = DEEP_RESEARCH
FUND_ANALYSIS = FUND_INVESTMENT_DECISION
GENERAL_CHAT = "general_chat"
DAILY_REPORT = "daily_report"

DAILY_REPORT_COMMANDS = {
    "投研日报",
    "生成投研日报",
    "生成日报",
    "今日投研日报",
    "固收投研日报",
    "生成固收日报",
    "今日固收日报",
}

FUND_KEYWORDS = (
    "基金",
    "公募",
    "私募",
    "ETF",
    "etf",
    "投决意见",
    "投委会意见",
    "产品研究",
)

RESEARCH_REPORT_KEYWORDS = (
    "深度报告",
    "专题报告",
    "专题研究",
    "写一篇",
    "撰写",
    "起草",
    "分析框架",
)

REPORT_ANALYSIS_KEYWORDS = (
    "研报",
    "研究报告",
    "报告解析",
    "解读报告",
    "分析这份报告",
    "分析这篇报告",
    "摘要",
    "总结",
    "提炼",
    "资料",
    "材料",
    "纪要",
    "核心结论",
    "投资逻辑",
)

SENTIMENT_KEYWORDS = (
    "舆情",
    "新闻",
    "消息",
    "负面",
    "正面",
    "事件",
    "动态",
    "跟踪",
    "热点",
)


def route_task(user_text: str) -> str:
    """Return a stable task type for a natural-language user message."""
    normalized = re.sub(r"\s+", " ", str(user_text or "")).strip()
    if not normalized:
        return GENERAL_CHAT

    if normalized in DAILY_REPORT_COMMANDS:
        return DAILY_REPORT

    # More specific intents must be checked before generic words such as
    # “报告”“分析” and “研究”.
    if any(keyword in normalized for keyword in FUND_KEYWORDS):
        return FUND_ANALYSIS

    if any(keyword in normalized for keyword in RESEARCH_REPORT_KEYWORDS):
        return RESEARCH_REPORT

    if any(keyword in normalized for keyword in REPORT_ANALYSIS_KEYWORDS):
        return REPORT_ANALYSIS

    if any(keyword in normalized for keyword in SENTIMENT_KEYWORDS):
        return SENTIMENT_ANALYSIS

    return GENERAL_CHAT
