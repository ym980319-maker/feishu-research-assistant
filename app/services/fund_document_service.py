"""Extract structured fund facts from already-parsed PDF or Word text."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import re
from typing import Any


FundDocumentTextInput = str | Sequence[str] | None

FUND_DOCUMENT_FIELDS = (
    "产品信息",
    "投资策略",
    "投资范围",
    "风险因素",
    "管理团队",
)

SECTION_ALIASES = {
    "产品信息": (
        "产品基本信息",
        "基金基本信息",
        "产品信息",
        "产品概况",
        "基金概况",
    ),
    "投资策略": ("投资策略", "主要投资策略"),
    "投资范围": ("投资范围", "基金投资范围"),
    "风险因素": (
        "风险因素",
        "主要风险",
        "风险揭示",
        "风险提示",
    ),
    "管理团队": (
        "基金经理及管理人",
        "基金经理与管理人",
        "基金管理人与基金经理",
        "管理团队",
        "基金管理人",
        "基金经理",
    ),
}

HEADING_PREFIX = re.compile(
    r"^(?:"
    r"第[一二三四五六七八九十百0-9]+[章节部分]\s*"
    r"|[一二三四五六七八九十百0-9]+[、.．)）]\s*"
    r")"
)
MARKDOWN_PREFIX = re.compile(r"^#{1,6}\s*")


def _normalize_documents(documents: FundDocumentTextInput) -> list[str]:
    if documents is None:
        return []
    if isinstance(documents, str):
        normalized = documents.strip()
        return [normalized] if normalized else []
    return [
        normalized
        for value in documents
        if (normalized := str(value or "").strip())
    ]


def _match_heading(line: str) -> tuple[str, str] | None:
    candidate = MARKDOWN_PREFIX.sub("", line.strip())
    candidate = HEADING_PREFIX.sub("", candidate).strip()
    for field, aliases in SECTION_ALIASES.items():
        for alias in aliases:
            match = re.fullmatch(
                rf"{re.escape(alias)}\s*(?:[：:]\s*(.*))?",
                candidate,
            )
            if match:
                inline_content = str(match.group(1) or "").strip()
                if (
                    inline_content
                    and field == "管理团队"
                    and alias in {"基金管理人", "基金经理"}
                ):
                    inline_content = f"{alias}：{inline_content}"
                return field, inline_content
    return None


def extract_fund_document_fields(
    documents: FundDocumentTextInput,
) -> dict[str, str]:
    """Extract five supported sections without inferring absent content."""
    collected: dict[str, list[str]] = {
        field: [] for field in FUND_DOCUMENT_FIELDS
    }
    for document in _normalize_documents(documents):
        current_field = ""
        for raw_line in document.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            heading = _match_heading(line)
            if heading is not None:
                current_field, inline_content = heading
                if inline_content:
                    collected[current_field].append(inline_content)
                continue
            if current_field:
                collected[current_field].append(line)

    return {
        field: "\n".join(parts).strip()
        for field, parts in collected.items()
    }


def fund_document_fields_to_json(fields: Mapping[str, Any]) -> str:
    """Return a stable five-field JSON object for downstream model input."""
    normalized = {
        field: str(fields.get(field) or "").strip()
        for field in FUND_DOCUMENT_FIELDS
    }
    return json.dumps(normalized, ensure_ascii=False, indent=2)


class FundDocumentService:
    """Structure text that has already been extracted from PDF/Word files."""

    def extract(self, documents: FundDocumentTextInput) -> dict[str, str]:
        return extract_fund_document_fields(documents)

    def extract_json(self, documents: FundDocumentTextInput) -> str:
        return fund_document_fields_to_json(self.extract(documents))
