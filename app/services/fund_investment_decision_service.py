"""Generate a source-grounded fund investment decision report."""

from __future__ import annotations

import asyncio
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
FUND_KIMI_TIMEOUT_SECONDS = 300.0
FUND_KIMI_MAX_ATTEMPTS = 2
FUND_KIMI_TIMEOUT_MESSAGE = (
    "基金尽调报告生成超时，已自动重试一次但仍未完成。"
    "请稍后重新提交，或适当精简材料后重试。"
)

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


def _is_kimi_timeout_response(result: str) -> bool:
    normalized = str(result or "").strip().lower()
    return "超时" in normalized and "kimi" in normalized


async def call_fund_kimi_with_retry(
    model_handler: ModelHandler,
    prompt: str,
    task_type: str,
) -> str:
    """Call the fund-analysis model with one timeout-only retry."""
    for attempt in range(FUND_KIMI_MAX_ATTEMPTS):
        try:
            result = await asyncio.wait_for(
                model_handler(prompt, task_type),
                timeout=FUND_KIMI_TIMEOUT_SECONDS,
            )
        except (TimeoutError, asyncio.TimeoutError):
            print(
                "基金分析 Kimi 调用超时:",
                f"attempt={attempt + 1}/{FUND_KIMI_MAX_ATTEMPTS}",
            )
        else:
            if not _is_kimi_timeout_response(result):
                return result
            print(
                "基金分析 Kimi 返回超时:",
                f"attempt={attempt + 1}/{FUND_KIMI_MAX_ATTEMPTS}",
            )

    return FUND_KIMI_TIMEOUT_MESSAGE


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

    formatted_documents = format_fund_documents(documents)
    if len(formatted_documents) > 12000:
        formatted_documents = (
            formatted_documents[:12000]
            + "\n\n【正文已截断】"
        )
    print("Kimi收到的正文长度:", len(formatted_documents))

    prompt = f"""
你是一名服务于银行、保险、理财子、券商资管和基金投委会的机构产品尽调研究员。
请严格依据以下输入，为该产品生成一份正式的机构投资者《产品尽调分析报告》：

【基金名称/用户请求】
{fund_name or '未提供基金名称'}

【基金合同、募集说明书或定期报告】
{formatted_documents}

【基金数据 Provider】
数据来源：{FUND_DATA_SOURCE}
{format_fund_data_for_prompt(fund_code, fund_data)}

{format_evidence_pool(evidence_pool)}

【基金公开资料状态】
{'已获得可核验公开资料。' if evidence_pool else '公开资料未找到'}

【知识库参考材料】
{knowledge_text or '暂无相关知识库材料。'}

你不是在做材料摘要，而是在为机构固收投资经理和投委会撰写内部产品尽调报告。
不得按原文顺序机械摘录。每个重要判断应尽量说明：材料披露了什么、对收益或风险的作用机制是什么、对机构组合有何影响、还需核查什么。

请严格按照以下 Markdown 结构输出，标题和六个一级章节不得改名、合并或省略：

# 产品尽调分析报告

## 一、产品概况

依据材料提取并简洁列示：
- 产品名称
- 管理人
- 产品类型
- 成立时间
- 规模
- 投资范围

未披露项直接标注“材料未披露”，不用常识补全。

## 二、投资策略与收益来源分析

先概括产品的核心收益逻辑，再结合实际披露分析：
- 产品主要收益来源
- 久期策略
- 信用策略
- 行业配置
- 区域配置
- 杠杆情况
- 衍生品或对冲工具使用情况

重点解释“产品靠什么获取收益”及其适用的市场环境，不要只罗列投资范围。
必须区分合同允许的投资范围、管理人的策略表述与实际持仓；材料未提供实际运作证据时，不得将合同上限或可投范围当作实际策略。

## 三、历史业绩分析

结合材料明确披露的时间区间和口径，分析：
- 历史收益表现
- 收益稳定性
- 波动情况
- 最大回撤（如材料提供）
- 与同类产品比较（如材料提供）

不得将区间收益改写为年化收益，不得自行计算材料未披露的指标。任一缺失项均标注“材料未披露”。

## 四、风险分析

不做通用风险清单，必须结合产品的投资范围、收益来源、持仓特征、开放安排和杠杆情况，分析风险暴露、传导路径及可能的组合影响：

1. 信用风险
2. 利率风险
3. 流动性风险
4. 汇率风险
5. 集中度风险
6. 策略失效风险

产品对某类风险的暴露无法由材料确认时，标注“材料未披露”，不得套用假设。

## 五、组合配置价值分析

站在机构固收投资经理角度，结合前述收益来源和风险暴露，分析：
- 该产品在组合中可能承担的作用
- 是否适合作为收益增强工具，以及所需前提条件
- 与利率债、信用债、可转债等资产的关系
- 适合的组合类型、风险偏好与持有期限

没有相关性、回撤或流动性数据时，只能给出有条件的定性判断，并明确说明证据不足。

## 六、投资结论

### 产品优势
提炼有材料支持的核心优势，不写空泛表述。

### 核心风险
按对机构组合的影响程度排序，指出可能使投资逻辑失效的关键情形。

### 配置建议
给出“建议配置、有条件配置、暂缓配置或材料不足无法判断”中的明确意见，说明适用组合、配置前提和后续跟踪指标，不得承诺确定性收益。

### 需要进一步尽调的问题
列出影响投资决策且当前材料尚未回答的具体问题。

强制要求：
1. 不允许编造基金规模、净值、收益率、排名、回撤、持仓、费率、基金经理履历或其他基金数据。
2. 无法从基金材料、Evidence Pool 或知识库确认的信息，必须明确标注“材料未披露”。
3. 所有公开信息必须引用对应来源；没有来源的内容不得作为事实输出。
4. 未检索到公开资料时，必须明确说明“公开资料未找到”，不得依据常识补写基金经理、管理人、监管或舆情事实。
5. 禁止输出虚假数据或占位符，包括 XX%、X亿元、Xbp、XX公司。
6. 投资建议必须区分事实、研究判断和待核实事项，不得承诺确定性收益。
7. 基金规模、净值、历史收益和持仓数据只能引用基金数据 Provider 或用户材料中的明确数据，并保留对应日期；Provider 未返回的字段必须说明缺失。
8. 所有产品事实均应说明来自用户材料、基金数据 Provider、Evidence Pool 或知识库中的哪一类材料；研究判断不得伪装成已披露事实。
9. 对材料未涉及的资产类别、策略、指标或团队信息，不得推定产品具备相关能力；不能因为本模板列出该项目就补写内容。
""".strip()

    return await call_fund_kimi_with_retry(
        model_handler,
        prompt,
        "基金产品研究",
    )
