"""Research-domain task categories independent from transport routing."""

from __future__ import annotations

from enum import Enum


class ResearchTaskType(str, Enum):
    SENTIMENT_RESEARCH = "舆情研究"
    COMPANY_RESEARCH = "公司研究"
    INDUSTRY_RESEARCH = "行业研究"
    MACRO_RESEARCH = "宏观研究"
    FUND_RESEARCH = "基金研究"
