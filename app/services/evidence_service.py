"""Build and format a verified evidence pool for research tasks."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from app.models.evidence import Evidence
from app.providers.public_search_provider import search_public_information


EvidenceResearcher = Callable[[str], Awaitable[Any]]
KnowledgeProvider = Callable[..., Awaitable[str]]
PUBLIC_INFO_GROUPS = ("announcements", "news", "regulatory_info")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _extract_candidates(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        if any(
            key in value
            for key in ("title", "content", "source", "url", "publish_time")
        ):
            return [value]

        candidates = []
        for group in PUBLIC_INFO_GROUPS:
            items = value.get(group)
            if isinstance(items, Sequence) and not isinstance(items, (str, bytes)):
                candidates.extend(item for item in items if isinstance(item, Mapping))
        return candidates

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [item for item in value if isinstance(item, Mapping)]
    return []


def evidence_from_search_result(value: Mapping[str, Any]) -> Evidence | None:
    """Convert one provider item to Evidence, rejecting untraceable content."""
    source = _text(value.get("source"))
    title = _text(value.get("title"))
    content = _text(value.get("content"))
    if not source or not (title or content):
        return None

    return Evidence(
        title=title,
        content=content,
        source=source,
        published_time=_text(
            value.get("published_time") or value.get("publish_time")
        ),
        url=_text(value.get("url")),
    )


async def collect_public_evidence(
    query: str,
    researcher: EvidenceResearcher | None = None,
) -> list[Evidence]:
    """Search, normalize, filter and prioritize public evidence."""
    selected_researcher = researcher or search_public_information
    try:
        raw_result = await selected_researcher(query)
    except Exception as exc:
        print("公开证据检索失败，使用空证据池继续研究:", type(exc).__name__)
        return []

    evidence_pool = []
    seen = set()
    for candidate in _extract_candidates(raw_result):
        evidence = evidence_from_search_result(candidate)
        if evidence is None:
            continue
        identity = (
            evidence.url,
            evidence.title,
            evidence.source,
            evidence.content,
        )
        if identity in seen:
            continue
        seen.add(identity)
        evidence_pool.append(evidence)

    # Missing publication time does not discard an otherwise sourced item;
    # it is placed behind evidence with a known publication time.
    evidence_pool.sort(key=lambda item: 0 if item.published_time else 1)
    return evidence_pool


async def collect_evidence_materials(
    query: str,
    knowledge_provider: KnowledgeProvider,
    researcher: EvidenceResearcher | None = None,
    *,
    include_public_info: bool = True,
) -> tuple[list[Evidence], str]:
    """Collect the evidence pool and relevant internal knowledge materials."""
    if include_public_info:
        evidence_pool = await collect_public_evidence(query, researcher)
    else:
        evidence_pool = []

    try:
        knowledge_text = await knowledge_provider(limit=10, user_text=query)
    except Exception as exc:
        print("读取知识库材料失败，使用空材料继续研究:", type(exc).__name__)
        knowledge_text = ""
    return evidence_pool, str(knowledge_text or "")


def format_evidence_pool(evidence_pool: Sequence[Evidence]) -> str:
    """Render only Evidence objects into the public-information prompt block."""
    parts = ["【公开信息证据池】"]
    if not evidence_pool:
        parts.append("未检索到公开资料")
    else:
        for index, evidence in enumerate(evidence_pool, start=1):
            parts.extend(
                [
                    f"\n【证据 {index}】",
                    f"标题：{evidence.title or '未提供标题'}",
                    f"内容：{evidence.content or '未提供内容'}",
                    f"来源：{evidence.source}",
                    "发布时间："
                    + (evidence.published_time or "未提供（低优先级）"),
                    f"来源链接：{evidence.url or '未提供'}",
                ]
            )

    parts.extend(
        [
            "\n【证据使用约束】",
            "基于以下公开信息生成，不允许编造事实。",
            "公开信息中的事实必须保留对应来源；无来源内容不得作为事实输出。",
            "未提供发布时间的证据只能作为低优先级线索，并明确说明时间缺失。",
            "禁止生成虚假数据或占位符：XX%、X亿元、Xbp、XX公司。",
        ]
    )
    return "\n".join(parts)


def build_evidence_research_prompt(
    message: str,
    evidence_pool: Sequence[Evidence],
    knowledge_text: str,
) -> str:
    return f"""
【用户原始输入】
{message}

{format_evidence_pool(evidence_pool)}

【知识库材料】
{knowledge_text or '暂无相关知识库材料。'}

请严格依据用户输入、Evidence Pool 和知识库材料完成研究分析；信息不足时明确说明，不得自行补全事实或数字。
""".strip()

