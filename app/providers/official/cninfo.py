from __future__ import annotations

import html
import json
import logging
import re
from datetime import datetime, timezone
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


class CninfoProvider(EvidenceProvider):
    name = "cninfo"
    allowed_hosts = frozenset(
        {"cninfo.com.cn", "www.cninfo.com.cn", "static.cninfo.com.cn"}
    )
    endpoint = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
    referer = "https://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice"

    @staticmethod
    def _column_for_code(stock_code: str) -> str:
        return "sse" if stock_code.startswith(("6", "68")) else "szse"

    @classmethod
    def _request_data(
        cls, stock_code: str, since: datetime, until: datetime, limit: int
    ) -> dict[str, str]:
        # This is the official site's front-end endpoint, not a stable public API.
        return {
            "pageNum": "1",
            "pageSize": str(min(max(limit * 3, 10), 30)),
            "column": cls._column_for_code(stock_code),
            "tabName": "fulltext",
            "plate": "",
            "stock": "",
            "searchkey": stock_code,
            "secid": "",
            "category": "",
            "trade": "",
            "seDate": f"{since.date().isoformat()}~{until.date().isoformat()}",
            "sortName": "time",
            "sortType": "desc",
            "isHLtitle": "true",
        }

    @staticmethod
    def _clean_title(value: Any) -> str:
        return html.unescape(re.sub(r"<[^>]+>", "", str(value or ""))).strip()

    @staticmethod
    def _published_at(value: Any) -> str | None:
        try:
            timestamp = float(value)
            if timestamp > 10_000_000_000:
                timestamp /= 1000
            return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
        except (TypeError, ValueError, OSError, OverflowError):
            return None

    @classmethod
    def _announcement_url(cls, value: Any) -> str:
        path = str(value or "").strip()
        if not path:
            return ""
        if path.startswith("https://"):
            url = path
        elif path.startswith("http://"):
            return ""
        else:
            url = f"https://static.cninfo.com.cn/{path.lstrip('/')}"
        return url if is_allowed_https_url(url, cls.allowed_hosts) else ""

    @classmethod
    def parse_announcements(
        cls,
        payload: dict[str, Any],
        subject: dict[str, Any],
        since: datetime,
        until: datetime,
        limit: int,
    ) -> list[Evidence]:
        stock_code = str(subject.get("stock_code") or "").strip()
        if not re.fullmatch(r"\d{6}", stock_code):
            return []
        raw_items = payload.get("announcements")
        if not isinstance(raw_items, list):
            return []

        since_utc = since.astimezone(timezone.utc)
        until_utc = until.astimezone(timezone.utc)
        retrieved_at = utc_now_iso()
        items: list[Evidence] = []
        seen_urls: set[str] = set()
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            returned_code = str(raw.get("secCode") or "").strip()
            if returned_code != stock_code:
                continue
            title = cls._clean_title(raw.get("announcementTitle"))
            url = cls._announcement_url(raw.get("adjunctUrl"))
            published_at = cls._published_at(raw.get("announcementTime"))
            if not title or not url or not published_at:
                continue
            published = datetime.fromisoformat(published_at)
            if published < since_utc or published > until_utc or url in seen_urls:
                continue
            seen_urls.add(url)
            items.append(
                Evidence(
                    title=title,
                    url=url,
                    source="巨潮资讯",
                    source_type="company_announcement",
                    published_at=published_at,
                    summary="",
                    document_type="上市公司公告",
                    issuer=str(raw.get("secName") or subject.get("issuer") or "").strip()
                    or None,
                    stock_code=stock_code,
                    retrieved_at=retrieved_at,
                    verification_status="metadata_verified",
                    source_priority=1,
                )
            )
        items.sort(key=lambda item: item.published_at or "", reverse=True)
        return items[: max(0, limit)]

    async def search(
        self,
        subject: dict[str, Any],
        since: datetime,
        until: datetime,
        limit: int,
    ) -> list[Evidence]:
        stock_code = str(subject.get("stock_code") or "").strip()
        if not re.fullmatch(r"\d{6}", stock_code) or limit <= 0:
            return []
        try:
            headers = {
                "Accept": "application/json, text/plain, */*",
                "Referer": self.referer,
                "User-Agent": "feishu-research-assistant/official-evidence",
            }
            max_bytes = env_int(
                "OFFICIAL_MAX_HTML_BYTES",
                DEFAULT_MAX_HTML_BYTES,
                minimum=1_024,
                maximum=10_000_000,
            )
            async with httpx.AsyncClient(timeout=httpx_timeout()) as client:
                result = await fetch_limited(
                    client,
                    "POST",
                    self.endpoint,
                    self.allowed_hosts,
                    max_bytes=max_bytes,
                    headers=headers,
                    data=self._request_data(stock_code, since, until, limit),
                )
            status = result.status_code if result else 0
            if not result or status != 200:
                logger.info("provider=%s status=%s candidates=0 valid=0", self.name, status)
                return []
            try:
                payload = json.loads(result.content.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                logger.info("provider=%s status=%s candidates=0 valid=0", self.name, status)
                return []
            candidates = payload.get("announcements")
            candidate_count = len(candidates) if isinstance(candidates, list) else 0
            items = self.parse_announcements(payload, subject, since, until, limit)
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

