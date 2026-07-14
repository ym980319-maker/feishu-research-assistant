from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.providers.base import env_int, is_allowed_https_url
from app.providers.formatter import format_evidence_for_report, format_evidence_index
from app.providers.models import Evidence
from app.providers.official.cninfo import CninfoProvider
from app.providers.official.miit import MiitProvider
from app.providers.orchestrator import (
    collect_official_evidence,
    deduplicate_evidence,
    extract_research_subject,
    filter_evidence,
    rank_evidence,
)


NOW = datetime(2026, 7, 14, 8, 0, tzinfo=timezone.utc)
SINCE = NOW - timedelta(days=30)


def evidence(**overrides: object) -> Evidence:
    values: dict[str, object] = {
        "title": "测试公告",
        "url": "https://static.cninfo.com.cn/finalpage/test.pdf",
        "source": "巨潮资讯",
        "source_type": "company_announcement",
        "published_at": "2026-07-10T08:00:00+00:00",
        "summary": "",
        "document_type": "上市公司公告",
        "issuer": "测试公司",
        "stock_code": "300750",
        "retrieved_at": "2026-07-14T08:00:00+00:00",
        "verification_status": "metadata_verified",
        "source_priority": 1,
    }
    values.update(overrides)
    return Evidence(**values)


class SubjectExtractionTests(unittest.TestCase):
    def test_command_cleanup_and_company_name_preserved(self) -> None:
        subject = extract_research_subject("生成深度报告 宁德时代")
        self.assertEqual(subject["query"], "宁德时代")
        self.assertEqual(subject["issuer"], "宁德时代")
        self.assertEqual(subject["subject_type"], "ambiguous")
        self.assertIsNone(subject["stock_code"])

    def test_six_digit_stock_code(self) -> None:
        subject = extract_research_subject("深度报告 宁德时代 300750")
        self.assertEqual(subject["subject_type"], "listed_company")
        self.assertEqual(subject["issuer"], "宁德时代")
        self.assertEqual(subject["stock_code"], "300750")
        self.assertEqual(subject["exchange"], "SZSE")

    def test_macro_or_industry_topic(self) -> None:
        subject = extract_research_subject("研报 新能源行业政策")
        self.assertEqual(subject["subject_type"], "topic")
        self.assertEqual(subject["query"], "新能源行业政策")


class SecurityAndModelTests(unittest.TestCase):
    def test_official_https_allowlist(self) -> None:
        allowed = CninfoProvider.allowed_hosts
        self.assertTrue(
            is_allowed_https_url("https://static.cninfo.com.cn/a.pdf", allowed)
        )
        self.assertFalse(is_allowed_https_url("http://static.cninfo.com.cn/a.pdf", allowed))
        self.assertFalse(is_allowed_https_url("https://example.com/a.pdf", allowed))
        self.assertFalse(
            is_allowed_https_url("https://static.cninfo.com.cn:8443/a.pdf", allowed)
        )
        self.assertFalse(
            is_allowed_https_url("https://evil.static.cninfo.com.cn/a.pdf", allowed)
        )

    def test_private_and_metadata_addresses_rejected(self) -> None:
        for host in ("127.0.0.1", "10.0.0.1", "192.168.1.1", "169.254.169.254"):
            self.assertFalse(is_allowed_https_url(f"https://{host}/", {host}))

    def test_evidence_serialization(self) -> None:
        item = evidence()
        restored = Evidence.from_dict(item.to_dict())
        self.assertEqual(restored, item)

    def test_invalid_verification_status_becomes_rejected(self) -> None:
        item = evidence(verification_status="invented")
        self.assertEqual(item.verification_status, "rejected")

    def test_invalid_numeric_config_uses_safe_default(self) -> None:
        with patch.dict(os.environ, {"OFFICIAL_MAX_DOCUMENTS": "not-a-number"}):
            self.assertEqual(env_int("OFFICIAL_MAX_DOCUMENTS", 10, minimum=1), 10)


class OrchestratorHelperTests(unittest.TestCase):
    def test_deduplicates_normalized_url_and_title(self) -> None:
        first = evidence()
        same_url = evidence(
            title="另一个标题",
            url="https://STATIC.CNINFO.COM.CN/finalpage/test.pdf#section",
        )
        same_title = evidence(url="https://static.cninfo.com.cn/finalpage/other.pdf")
        self.assertEqual(len(deduplicate_evidence([first, same_url, same_title])), 1)

    def test_company_announcement_ranks_before_policy(self) -> None:
        policy = evidence(
            title="行业政策",
            url="https://www.miit.gov.cn/policy/1.html",
            source="工业和信息化部",
            source_type="government_policy",
            source_priority=3,
            verification_status="content_verified",
        )
        company = evidence(verification_status="metadata_verified")
        self.assertEqual(rank_evidence([policy, company])[0], company)

    def test_content_verified_ranks_first_with_same_source(self) -> None:
        metadata = evidence(title="公告甲", url="https://static.cninfo.com.cn/a.pdf")
        content = evidence(
            title="公告乙",
            url="https://static.cninfo.com.cn/b.pdf",
            verification_status="content_verified",
        )
        self.assertEqual(rank_evidence([metadata, content])[0], content)

    def test_time_range_filter(self) -> None:
        old = evidence(published_at="2025-01-01T00:00:00+00:00")
        current = evidence()
        undated = evidence(
            title="无日期公告", url="https://static.cninfo.com.cn/undated.pdf", published_at=None
        )
        self.assertEqual(filter_evidence([old, current, undated], SINCE, NOW), [current, undated])


class ProviderParsingTests(unittest.TestCase):
    def test_cninfo_json_normalization(self) -> None:
        payload = {
            "announcements": [
                {
                    "secCode": "300750",
                    "secName": "宁德时代",
                    "announcementTitle": "<em>宁德时代</em>：董事会决议公告",
                    "announcementTime": int(
                        datetime(2026, 7, 10, tzinfo=timezone.utc).timestamp() * 1000
                    ),
                    "adjunctUrl": "finalpage/2026-07-10/test.PDF",
                },
                {
                    "secCode": "000001",
                    "secName": "其他公司",
                    "announcementTitle": "无关公告",
                    "announcementTime": int(NOW.timestamp() * 1000),
                    "adjunctUrl": "finalpage/other.PDF",
                },
            ]
        }
        items = CninfoProvider.parse_announcements(
            payload,
            {"stock_code": "300750", "issuer": "宁德时代"},
            SINCE,
            NOW,
            10,
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "宁德时代：董事会决议公告")
        self.assertEqual(items[0].verification_status, "metadata_verified")
        self.assertEqual(items[0].summary, "")
        self.assertTrue(items[0].url.startswith("https://static.cninfo.com.cn/"))

    def test_cninfo_requires_stock_code(self) -> None:
        items = CninfoProvider.parse_announcements(
            {"announcements": []}, {"issuer": "宁德时代"}, SINCE, NOW, 10
        )
        self.assertEqual(items, [])

    def test_miit_rss_parsing_and_relevance(self) -> None:
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <rss><channel>
          <item>
            <title>新能源汽车行业管理通知</title>
            <link>https://www.miit.gov.cn/zwgk/zcwj/test.html</link>
            <pubDate>Fri, 10 Jul 2026 08:00:00 GMT</pubDate>
            <description><![CDATA[关于新能源汽车行业管理工作的通知。]]></description>
          </item>
          <item>
            <title>无关信息</title>
            <link>https://www.miit.gov.cn/zwgk/other.html</link>
            <pubDate>Fri, 10 Jul 2026 08:00:00 GMT</pubDate>
            <description>其他内容</description>
          </item>
        </channel></rss>""".encode("utf-8")
        items = MiitProvider.parse_rss(
            xml,
            {"query": "新能源汽车", "issuer": None, "stock_code": None},
            SINCE,
            NOW,
            10,
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].source_type, "government_policy")
        self.assertEqual(items[0].verification_status, "metadata_verified")
        self.assertIn("新能源汽车", items[0].summary)

    def test_miit_invalid_xml_degrades_to_empty(self) -> None:
        self.assertEqual(
            MiitProvider.parse_rss(
                b"<not-closed>",
                {"query": "政策"},
                SINCE,
                NOW,
                10,
            ),
            [],
        )

    def test_miit_undated_record_sorts_after_dated(self) -> None:
        xml = """<rss><channel>
          <item><title>新能源政策甲</title><link>https://www.miit.gov.cn/a.html</link>
          <description>新能源</description></item>
          <item><title>新能源政策乙</title><link>https://www.miit.gov.cn/b.html</link>
          <pubDate>Fri, 10 Jul 2026 08:00:00 GMT</pubDate><description>新能源</description></item>
        </channel></rss>""".encode("utf-8")
        items = MiitProvider.parse_rss(xml, {"query": "新能源"}, SINCE, NOW, 10)
        self.assertIsNotNone(items[0].published_at)
        self.assertIsNone(items[1].published_at)


class _FailingProvider:
    name = "failing"

    async def search(self, subject: dict, since: datetime, until: datetime, limit: int):
        raise RuntimeError("provider unavailable")


class OrchestratorAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_exception_degrades_to_empty(self) -> None:
        with patch(
            "app.providers.orchestrator.official_research_enabled", return_value=True
        ), patch(
            "app.providers.orchestrator.get_enabled_providers",
            return_value=[_FailingProvider()],
        ):
            self.assertEqual(await collect_official_evidence("深度报告 300750"), [])

    async def test_disabled_feature_does_not_build_or_call_providers(self) -> None:
        with patch(
            "app.providers.orchestrator.official_research_enabled", return_value=False
        ), patch(
            "app.providers.orchestrator.get_enabled_providers"
        ) as provider_factory:
            self.assertEqual(await collect_official_evidence("深度报告 300750"), [])
            provider_factory.assert_not_called()

    async def test_default_environment_switch_is_off(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(await collect_official_evidence("深度报告 300750"), [])


class FormatterTests(unittest.TestCase):
    def test_full_evidence_format(self) -> None:
        output = format_evidence_for_report([evidence()])
        self.assertIn("【官方资料 Evidence】", output)
        self.assertIn("Evidence 1", output)
        self.assertIn("正文未读取，仅核验公告/政策元数据", output)
        self.assertIn("metadata_verified", output)
        self.assertIn("https://static.cninfo.com.cn/finalpage/test.pdf", output)

    def test_evidence_index_format(self) -> None:
        output = format_evidence_index([evidence()])
        self.assertIn("【官方资料索引】", output)
        self.assertIn("1. 来源：巨潮资讯", output)
        self.assertNotIn("摘要：", output)

    def test_empty_format(self) -> None:
        self.assertEqual(
            format_evidence_for_report([]),
            "【官方资料 Evidence】\n本次未获取到有效官方资料。",
        )


if __name__ == "__main__":
    unittest.main()
