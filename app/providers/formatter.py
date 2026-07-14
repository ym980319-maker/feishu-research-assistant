from __future__ import annotations

from .models import Evidence


EMPTY_SUMMARY = "正文未读取，仅核验公告/政策元数据"


def format_evidence_for_report(items: list[Evidence]) -> str:
    lines = ["【官方资料 Evidence】"]
    if not items:
        return "\n".join(lines + ["本次未获取到有效官方资料。"])
    for index, item in enumerate(items, start=1):
        lines.extend(
            [
                "",
                f"Evidence {index}",
                f"来源：{item.source}",
                f"资料类型：{item.document_type or item.source_type}",
                f"发布日期：{item.published_at or '未提供'}",
                f"标题：{item.title}",
                f"摘要：{item.summary or EMPTY_SUMMARY}",
                f"官方链接：{item.url}",
                f"验证状态：{item.verification_status}",
            ]
        )
    return "\n".join(lines)


def format_evidence_index(items: list[Evidence]) -> str:
    lines = ["【官方资料索引】"]
    if not items:
        return "\n".join(lines + ["本次未获取到有效官方资料。"])
    for index, item in enumerate(items, start=1):
        lines.extend(
            [
                "",
                f"{index}. 来源：{item.source}",
                f"发布日期：{item.published_at or '未提供'}",
                f"标题：{item.title}",
                f"官方链接：{item.url}",
                f"验证状态：{item.verification_status}",
            ]
        )
    return "\n".join(lines)

