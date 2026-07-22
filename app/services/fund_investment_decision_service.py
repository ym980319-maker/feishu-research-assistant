"""Generate a source-grounded fund investment decision report."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
import json
import re

from app.models.evidence import Evidence
from app.providers.fund_data_provider import (
    FUND_DATA_SOURCE,
    FundDataProvider,
)
from app.services.evidence_service import (
    EvidenceResearcher,
    KnowledgeProvider,
    collect_public_evidence,
    format_evidence_pool,
)


ModelHandler = Callable[[str, str], Awaitable[str]]
FundDocumentInput = str | Sequence[str] | None
FUND_CODE_IN_TEXT = re.compile(r"(?<!\d)(\d{6})(?!\d)")

FUND_PUBLIC_SEARCH_TOPICS = (
    "基金经理公开信息",
    "基金管理人公告",
    "监管信息",
    "新闻舆情",
)


def build_fund_public_queries(fund_name: str) -> tuple[str, ...]:
    normalized_name = str(fund_name or "").strip()
    return tuple(
        f"{normalized_name} {topic}".strip()
        for topic in FUND_PUBLIC_SEARCH_TOPICS
    )


def _evidence_identity(evidence: Evidence) -> tuple[str, str, str, str]:
    return (
        evidence.url,
        evidence.title,
        evidence.source,
        evidence.content,
    )


async def collect_fund_evidence(
    fund_name: str,
    researcher: EvidenceResearcher | None = None,
) -> list[Evidence]:
    """Collect four required public-information dimensions for a fund."""
    evidence_pool = []
    seen = set()
    for query in build_fund_public_queries(fund_name):
        for evidence in await collect_public_evidence(query, researcher):
            identity = _evidence_identity(evidence)
            if identity in seen:
                continue
            seen.add(identity)
            evidence_pool.append(evidence)

    evidence_pool.sort(key=lambda item: 0 if item.published_time else 1)
    return evidence_pool


def format_fund_documents(documents: FundDocumentInput) -> str:
    if documents is None:
        return "未提供基金合同、募集说明书或定期报告。"
    if isinstance(documents, str):
        normalized = documents.strip()
        return normalized or "未提供基金合同、募集说明书或定期报告。"

    parts = []
    for index, document in enumerate(documents, start=1):
        normalized = str(document or "").strip()
        if normalized:
            parts.append(f"【基金材料 {index}】\n{normalized}")
    return "\n\n".join(parts) or "未提供基金合同、募集说明书或定期报告。"


def extract_fund_code(message: str) -> str:
    match = FUND_CODE_IN_TEXT.search(str(message or ""))
    return match.group(1) if match else ""


async def collect_fund_data(
    message: str,
    provider: FundDataProvider | None = None,
) -> tuple[str, dict[str, object] | None]:
    """Read real fund data only when a six-digit code is present."""
    fund_code = extract_fund_code(message)
    if not fund_code:
        return "", None
    selected_provider = provider or FundDataProvider()
    try:
        return fund_code, await selected_provider.get_fund_data(fund_code)
    except Exception as exc:
        print("基金数据 Provider 调用失败，使用缺数说明继续:", type(exc).__name__)
        return fund_code, None


def format_fund_data_for_prompt(
    fund_code: str,
    fund_data: dict[str, object] | None,
) -> str:
    if not fund_code:
        return "未提供有效的6位基金代码，未调用基金数据 Provider。"
    if not fund_data:
        return f"基金代码 {fund_code} 的公开基金数据暂不可用。"
    return json.dumps(fund_data, ensure_ascii=False, indent=2)


async def generate_fund_investment_decision(
    fund_name: str,
    model_handler: ModelHandler,
    knowledge_provider: KnowledgeProvider,
    *,
    documents: FundDocumentInput = None,
    evidence_researcher: EvidenceResearcher | None = None,
    fund_data_provider: FundDataProvider | None = None,
) -> str:
    """Collect Evidence and invoke Kimi once for the final decision report."""
    fund_code, fund_data = await collect_fund_data(
        fund_name,
        fund_data_provider,
    )
    evidence_pool = await collect_fund_evidence(
        fund_name,
        evidence_researcher,
    )
    try:
        knowledge_text = await knowledge_provider(
            limit=10,
            user_text=fund_name,
        )
    except Exception as exc:
        print("读取基金研究知识库材料失败，使用空材料继续:", type(exc).__name__)
        knowledge_text = ""

    prompt = f"""
请为以下基金生成一份正式的《基金投资决策报告》：

【基金名称/用户请求】
{fund_name or '未提供基金名称'}

【基金合同、募集说明书或定期报告】
{format_fund_documents(documents)}

【基金数据 Provider】
数据来源：{FUND_DATA_SOURCE}
{format_fund_data_for_prompt(fund_code, fund_data)}

{format_evidence_pool(evidence_pool)}

【基金公开资料状态】
{'已获得可核验公开资料。' if evidence_pool else '公开资料未找到'}

【知识库参考材料】
{knowledge_text or '暂无相关知识库材料。'}

请严格按照以下结构输出，标题不得改名或省略：

一、产品基本信息
二、管理人分析
三、基金经理分析
四、投资策略分析
五、历史业绩分析
六、风险分析
七、市场环境分析
八、投资建议

强制要求：
1. 不允许编造基金规模、净值、收益率、排名、回撤、持仓、费率、基金经理履历或其他基金数据。
2. 无法从基金材料、Evidence Pool 或知识库确认的数据，必须明确说明“数据缺失”或“未提供相关资料”。
3. 所有公开信息必须引用对应来源；没有来源的内容不得作为事实输出。
4. 未检索到公开资料时，必须明确说明“公开资料未找到”，不得依据常识补写基金经理、管理人、监管或舆情事实。
5. 禁止输出虚假数据或占位符，包括 XX%、X亿元、Xbp、XX公司。
6. 投资建议必须区分事实、研究判断和待核实事项，不得承诺确定性收益。
7. 基金规模、净值、历史收益和持仓数据只能引用基金数据 Provider 或用户材料中的明确数据，并保留对应日期；Provider 未返回的字段必须说明缺失。
""".strip()

    return await model_handler(prompt, "基金产品研究")
