from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .base import env_int, is_allowed_https_url
from .models import Evidence
from .official.cninfo import CninfoProvider
from .official.miit import MiitProvider
from .registry import get_enabled_providers, official_research_enabled


OFFICIAL_ALLOWED_HOSTS = CninfoProvider.allowed_hosts | MiitProvider.allowed_hosts
COMMAND_PATTERN = re.compile(r"(?:生成深度报告|深度报告|生成研报|研报)")
STOCK_CODE_PATTERN = re.compile(r"(?<!\d)(\d{6})(?!\d)")
TOPIC_MARKERS = (
    "宏观",
    "行业",
    "产业",
    "产业链",
    "政策",
    "市场",
    "经济",
    "通胀",
    "利率",
    "汇率",
    "新能源",
    "人工智能",
    "半导体",
)


def extract_research_subject(user_text: str) -> dict[str, str | None]:
    original = str(user_text or "")
    cleaned = re.sub(r"\s+", " ", COMMAND_PATTERN.sub(" ", original)).strip(" ：:，,。\t\r\n")
    code_match = STOCK_CODE_PATTERN.search(cleaned)
    stock_code = code_match.group(1) if code_match else None
    issuer = cleaned
    if stock_code:
        issuer = STOCK_CODE_PATTERN.sub(" ", issuer)
        issuer = re.sub(r"\s+", " ", issuer).strip(" ：:，,。")
        exchange = _exchange_for_a_share_code(stock_code)
        return {
            "subject_type": "listed_company",
            "issuer": issuer or None,
            "stock_code": stock_code,
            "exchange": exchange,
            "query": cleaned,
        }
    if cleaned and any(marker in cleaned for marker in TOPIC_MARKERS):
        subject_type = "topic"
    else:
        subject_type = "ambiguous"
    return {
        "subject_type": subject_type,
        "issuer": cleaned or None,
        "stock_code": None,
        "exchange": None,
        "query": cleaned,
    }


def _exchange_for_a_share_code(stock_code: str) -> str | None:
    if stock_code.startswith(("6", "68")):
        return "SSE"
    if stock_code.startswith(("0", "3")):
        return "SZSE"
    if stock_code.startswith(("4", "8", "92")):
        return "BSE"
    return None


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def canonicalize_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        return ""
    port = parsed.port
    netloc = host if port in (None, 443) else f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return urlunsplit((parsed.scheme.lower(), netloc, path, query, ""))


def canonicalize_title(title: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", title, flags=re.UNICODE).lower()


def filter_evidence(
    items: Iterable[Evidence], since: datetime, until: datetime
) -> list[Evidence]:
    since_utc = since.astimezone(timezone.utc)
    until_utc = until.astimezone(timezone.utc)
    valid: list[Evidence] = []
    for item in items:
        if (
            not item.title
            or not item.url
            or item.verification_status == "rejected"
            or not is_allowed_https_url(item.url, OFFICIAL_ALLOWED_HOSTS)
        ):
            continue
        published = parse_iso_datetime(item.published_at)
        if published and (published < since_utc or published > until_utc):
            continue
        valid.append(item)
    return valid


def deduplicate_evidence(items: Iterable[Evidence]) -> list[Evidence]:
    unique: list[Evidence] = []
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    for item in items:
        url_key = canonicalize_url(item.url)
        title_key = canonicalize_title(item.title)
        if not url_key or not title_key or url_key in seen_urls or title_key in seen_titles:
            continue
        seen_urls.add(url_key)
        seen_titles.add(title_key)
        unique.append(item)
    return unique


def rank_evidence(items: Iterable[Evidence]) -> list[Evidence]:
    source_type_rank = {"company_announcement": 0, "government_policy": 1}
    verification_rank = {
        "content_verified": 0,
        "metadata_verified": 1,
        "content_unavailable": 2,
        "rejected": 3,
    }

    def sort_key(item: Evidence) -> tuple[int, int, int, float]:
        published = parse_iso_datetime(item.published_at)
        timestamp = published.timestamp() if published else float("-inf")
        return (
            source_type_rank.get(item.source_type, 99),
            item.source_priority,
            verification_rank.get(item.verification_status, 99),
            -timestamp,
        )

    return sorted(items, key=sort_key)


async def collect_official_evidence(
    user_text: str,
    lookback_days: int | None = None,
    limit: int | None = None,
) -> list[Evidence]:
    if not official_research_enabled():
        return []
    providers = get_enabled_providers()
    if not providers:
        return []

    days = (
        env_int("OFFICIAL_LOOKBACK_DAYS", 30, minimum=1, maximum=365)
        if lookback_days is None
        else max(1, min(int(lookback_days), 365))
    )
    max_documents = (
        env_int("OFFICIAL_MAX_DOCUMENTS", 10, minimum=1, maximum=50)
        if limit is None
        else max(1, min(int(limit), 50))
    )
    until = datetime.now(timezone.utc)
    since = until - timedelta(days=days)
    subject = extract_research_subject(user_text)

    results = await asyncio.gather(
        *(provider.search(subject, since, until, max_documents) for provider in providers),
        return_exceptions=True,
    )
    collected: list[Evidence] = []
    for result in results:
        if isinstance(result, BaseException):
            continue
        collected.extend(item for item in result if isinstance(item, Evidence))
    valid = filter_evidence(collected, since, until)
    return rank_evidence(deduplicate_evidence(valid))[:max_documents]

