"""Public-information research layer with a replaceable provider."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

from app.providers.public_search_provider import (
    has_traceable_source,
    normalize_public_search_result,
    search_public_information,
)


PUBLIC_INFO_FIELDS = ("announcements", "news", "regulatory_info")
PUBLIC_INFO_REQUEST_MARKERS = (
    "公开信息",
    "外部信息",
    "最新",
    "近期",
    "公告",
    "新闻",
    "监管",
    "政策",
    "动态",
    "进展",
    "核实",
    "验证",
    "补充",
)


class PublicInfoProvider(Protocol):
    """Interface implemented by current mock and future search providers."""

    async def research(self, subject: str) -> Mapping[str, Any]: ...


class MockPublicInfoProvider:
    """In-memory provider used until a real public-search API is connected."""

    def __init__(self, results: Mapping[str, Mapping[str, Any]] | None = None):
        self._results = dict(results or {})

    async def research(self, subject: str) -> Mapping[str, Any]:
        return self._results.get(subject, {})


class PublicSearchInfoProvider:
    """Adapt the unified public-search provider to PR18's grouped contract."""

    async def research(self, subject: str) -> Mapping[str, Any]:
        result = await search_public_information(subject)
        news = [result] if has_traceable_source(result) else []
        return {
            "subject": subject,
            "announcements": [],
            "news": news,
            "regulatory_info": [],
        }


DEFAULT_PUBLIC_INFO_PROVIDER: PublicInfoProvider = PublicSearchInfoProvider()


def empty_public_info(subject: str) -> dict[str, Any]:
    return {
        "subject": subject,
        "announcements": [],
        "news": [],
        "regulatory_info": [],
    }


def public_information_requested(message: str) -> bool:
    normalized = str(message or "").strip()
    return any(marker in normalized for marker in PUBLIC_INFO_REQUEST_MARKERS)


def normalize_public_info(
    subject: str,
    value: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Keep the service response stable even when a provider is incomplete."""
    result = empty_public_info(subject)
    if not isinstance(value, Mapping):
        return result

    provider_subject = str(value.get("subject") or subject).strip()
    result["subject"] = provider_subject or subject
    for field in PUBLIC_INFO_FIELDS:
        items = value.get(field)
        if isinstance(items, (list, tuple)):
            result[field] = [
                normalize_public_search_result(item)
                for item in items
                if isinstance(item, Mapping) and has_traceable_source(item)
            ]
    return result


async def research_public_info(
    subject: str,
    provider: PublicInfoProvider | None = None,
) -> dict[str, Any]:
    """Return structured public information without making network requests."""
    normalized_subject = str(subject or "").strip()
    selected_provider = provider or DEFAULT_PUBLIC_INFO_PROVIDER
    try:
        value = await selected_provider.research(normalized_subject)
    except Exception as exc:
        print("公开信息查询失败，使用空结果继续研究:", type(exc).__name__)
        value = None
    return normalize_public_info(normalized_subject, value)


def format_public_info(public_info: Mapping[str, Any]) -> str:
    """Format provider output as a bounded, explicit prompt section."""
    subject = str(public_info.get("subject") or "").strip()
    parts = [
        f"研究主体：{subject or '未识别'}",
        "来源约束：外部信息必须同时标注来源和发布时间；无来源或无时间的信息不得作为事实输出。",
    ]
    labels = {
        "announcements": "公告",
        "news": "新闻",
        "regulatory_info": "监管信息",
    }
    for field in PUBLIC_INFO_FIELDS:
        items = public_info.get(field)
        if isinstance(items, (list, tuple)) and items:
            rendered = json.dumps(list(items), ensure_ascii=False, indent=2)
        else:
            rendered = "暂无"
        parts.append(f"{labels[field]}：{rendered}")
    return "\n".join(parts)


KnowledgeProvider = Callable[..., Awaitable[str]]
PublicInfoResearcher = Callable[[str], Awaitable[dict[str, Any]]]


async def collect_research_materials(
    subject: str,
    knowledge_provider: KnowledgeProvider,
    public_info_researcher: PublicInfoResearcher = research_public_info,
    *,
    include_public_info: bool = True,
) -> tuple[dict[str, Any], str]:
    """Collect enabled public information, then relevant knowledge-base text."""
    if include_public_info:
        public_info = await public_info_researcher(subject)
    else:
        public_info = empty_public_info(subject)
    try:
        knowledge_text = await knowledge_provider(limit=10, user_text=subject)
    except Exception as exc:
        print("读取知识库材料失败，使用空材料继续研究:", type(exc).__name__)
        knowledge_text = ""
    return public_info, str(knowledge_text or "")


def build_research_prompt(
    message: str,
    public_info: Mapping[str, Any],
    knowledge_text: str,
) -> str:
    """Merge user input, public information and knowledge-base materials."""
    return f"""
【用户原始输入】
{message}

【公开信息补充】
{format_public_info(public_info)}

【知识库材料】
{knowledge_text or '暂无相关知识库材料。'}

请仅依据用户输入和以上材料进行分析。公开信息为空时不得自行补充或编造；引用外部信息时必须保留来源和发布时间，无来源的信息不得作为事实输出。
""".strip()
