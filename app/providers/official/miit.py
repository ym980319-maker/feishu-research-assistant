from __future__ import annotations

import html
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from ..base import (
    DEFAULT_MAX_HTML_BYTES,
    EvidenceProvider,
    env_int,
    fetch_limited,
    httpx_timeout,
    is_allowed_https_url,
)
from ..models import Evidence, utc_now_iso


logger = logging.getLogger(__name__)


class MiitProvider(EvidenceProvider):
    name = "miit"
    allowed_hosts = frozenset({"miit.gov.cn", "www.miit.gov.cn", "wap.miit.gov.cn"})
    rss_url = (
        "https://wap.miit.gov.cn/api-gateway/jpaas-plugins-web-server/front/rss/getinfo"
        "?webId=8d828e408d90447786ddbe128d495e9e"
        "&columnIds=925fa8f4afd44e53818794ed96d9876e,30f92eeafcfd4685984dfb793a2c5fff"
    )

    @staticmethod
    def _text(value: Any) -> str:
        cleaned = re.sub(r"<[^>]+>", " ", str(value or ""))
        return re.sub(r"\s+", " ", html.unescape(cleaned)).strip()

    @staticmethod
    def _date(value: Any) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, OverflowError):
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _keywords(subject: dict[str, Any]) -> list[str]:
        values = [subject.get("query"), subject.get("issuer"), subject.get("stock_code")]
        keywords: list[str] = []
        for value in values:
            text = str(value or "").strip()
            if not text:
                continue
            keywords.append(text.lower())
            keywords.extend(
                token.lower()
                for token in re.split(r"[\s,，、;；/]+", text)
                if len(token.strip()) >= 2
            )
        return list(dict.fromkeys(keywords))

    @staticmethod
    def _document_type(title: str) -> str:
        for marker in ("公告", "通知", "意见", "办法", "规划", "政策"):
            if marker in title:
                return marker
        return "行业信息"

    @classmethod
    def parse_rss(
        cls,
        content: bytes,
        subject: dict[str, Any],
        since: datetime,
        until: datetime,
        limit: int,
    ) -> list[Evidence]:
        try:
            root = ET.fromstring(content)
        except (ET.ParseError, ValueError):
            return []
        keywords = cls._keywords(subject)
        if not keywords:
            return []
        since_utc = since.astimezone(timezone.utc)
        until_utc = until.astimezone(timezone.utc)
        retrieved_at = utc_now_iso()
        items: list[Evidence] = []
        seen: set[tuple[str, str]] = set()

        for node in root.findall(".//item"):
            title = cls._text(node.findtext("title"))
            link = str(node.findtext("link") or "").strip()
            summary = cls._text(node.findtext("description"))
            searchable = f"{title} {summary}".lower()
            if not title or not link or not any(key in searchable for key in keywords):
                continue
            if not is_allowed_https_url(link, cls.allowed_hosts):
                continue
            published = cls._date(node.findtext("pubDate"))
            if published and (published < since_utc or published > until_utc):
                continue
            identity = (link, re.sub(r"\s+", "", title).lower())
            if identity in seen:
                continue
            seen.add(identity)
            items.append(
                Evidence(
                    title=title,
                    url=link,
                    source="工业和信息化部",
                    source_type="government_policy",
                    published_at=published.isoformat() if published else None,
                    summary=summary,
                    document_type=cls._document_type(title),
                    issuer=None,
                    stock_code=None,
                    retrieved_at=retrieved_at,
                    verification_status="metadata_verified",
                    source_priority=3,
                )
            )
        items.sort(
            key=lambda item: (item.published_at is not None, item.published_at or ""),
            reverse=True,
        )
        return items[: max(0, limit)]

    async def search(
        self,
        subject: dict[str, Any],
        since: datetime,
        until: datetime,
        limit: int,
    ) -> list[Evidence]:
        if limit <= 0:
            return []
        try:
            max_bytes = env_int(
                "OFFICIAL_MAX_HTML_BYTES",
                DEFAULT_MAX_HTML_BYTES,
                minimum=1_024,
                maximum=10_000_000,
            )
            async with httpx.AsyncClient(timeout=httpx_timeout()) as client:
                result = await fetch_limited(
                    client,
                    "GET",
                    self.rss_url,
                    self.allowed_hosts,
                    max_bytes=max_bytes,
                    headers={
                        "Accept": "application/rss+xml, application/xml, text/xml",
                        "User-Agent": "feishu-research-assistant/official-evidence",
                    },
                )
            status = result.status_code if result else 0
            if not result or status != 200:
                logger.info("provider=%s status=%s candidates=0 valid=0", self.name, status)
                return []
            items = self.parse_rss(result.content, subject, since, until, limit)
            try:
                candidate_count = len(ET.fromstring(result.content).findall(".//item"))
            except ET.ParseError:
                candidate_count = 0
            logger.info(
                "provider=%s status=%s candidates=%s valid=%s",
                self.name,
                status,
                candidate_count,
                len(items),
            )
            return items
        except Exception:
            logger.info("provider=%s status=error candidates=0 valid=0", self.name)
            return []

