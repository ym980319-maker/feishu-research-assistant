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
你是一名服务于银行、保险、理财子、券商资管和基金投委会的机构产品尽调研究员。
请严格依据以下输入，为该产品生成一份正式的机构投资者《产品尽调分析报告》：

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

请严格按照以下 Markdown 结构输出，标题、章节和小标题不得改名、合并或省略：

# 产品尽调分析报告

## 一、产品基本信息

逐项提取并列示：
- 产品名称
- 管理人
- 产品类型
- 成立时间
- 产品规模
- 投资范围
- 开放安排
- 管理团队

## 二、产品定位与投资逻辑

核心回答“这个产品靠什么赚钱？”。结合材料中实际披露的策略，分析收益来源是否包括：
- 久期收益
- 信用利差
- 权益弹性
- 转债增强
- 资产配置
- 管理人主动管理能力

未披露的收益来源不得推定为产品实际策略。

## 三、投资策略拆解

逐项分析：
- 投资范围：利率债、信用债、可转债、股票、ABS、其他资产
- 仓位管理
- 久期管理
- 信用筛选
- 行业配置
- 杠杆策略

必须区分合同允许投资的范围、管理人的策略表述和实际持仓，不得混为一谈。

## 四、历史表现与风险指标

逐项列示：
- 历史收益
- 年化收益
- 最大回撤
- 波动率
- 夏普比率
- 收益稳定性

任一指标未在用户材料或基金数据 Provider 中明确披露时，必须在该指标后写：
“材料未披露，无法判断。”
不得利用常识估算、根据净值走势自行计算，或把区间收益改写成年化收益。

## 五、资产配置与组合价值

站在机构投资者组合配置视角，分析该产品可能承担的作用：
- 固收增强
- 另类收益
- 权益替代
- 流动性管理

分析产品与传统利率债、信用债、股票及现金类资产的相关性。没有相关性数据时必须明确数据不足，只能给出有条件的定性判断。

## 六、风险分析

### 市场风险
- 利率风险
- 权益风险
- 汇率风险

### 信用风险
- 信用评级
- 行业集中度
- 单一主体风险

### 流动性风险
- 开放安排
- 底层资产流动性

### 策略风险
- 策略失效
- 容量限制
- 管理依赖

## 七、管理人与团队分析

逐项分析：
- 管理规模
- 投研体系
- 历史产品表现
- 投资风格稳定性
- 团队稳定性

## 八、投资价值判断

### 配置价值
1.
2.
3.

### 主要风险
1.
2.
3.

### 建议进一步核查事项
1.
2.
3.

强制要求：
1. 不允许编造基金规模、净值、收益率、排名、回撤、持仓、费率、基金经理履历或其他基金数据。
2. 无法从基金材料、Evidence Pool 或知识库确认的数据，必须明确说明“数据缺失”或“未提供相关资料”。
3. 所有公开信息必须引用对应来源；没有来源的内容不得作为事实输出。
4. 未检索到公开资料时，必须明确说明“公开资料未找到”，不得依据常识补写基金经理、管理人、监管或舆情事实。
5. 禁止输出虚假数据或占位符，包括 XX%、X亿元、Xbp、XX公司。
6. 投资建议必须区分事实、研究判断和待核实事项，不得承诺确定性收益。
7. 基金规模、净值、历史收益和持仓数据只能引用基金数据 Provider 或用户材料中的明确数据，并保留对应日期；Provider 未返回的字段必须说明缺失。
8. 所有产品事实均应说明来自用户材料、基金数据 Provider、Evidence Pool 或知识库中的哪一类材料；研究判断不得伪装成已披露事实。
9. 对材料未涉及的资产类别、策略、指标或团队信息，不得推定产品具备相关能力；不能因为本模板列出该项目就补写内容。
""".strip()

    return await model_handler(prompt, "基金产品研究")
