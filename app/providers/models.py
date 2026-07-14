from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from typing import Any


VERIFICATION_STATUSES = frozenset(
    {
        "metadata_verified",
        "content_verified",
        "content_unavailable",
        "rejected",
    }
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Evidence:
    title: str = ""
    url: str = ""
    source: str = ""
    source_type: str = ""
    published_at: str | None = None
    summary: str = ""
    document_type: str = ""
    issuer: str | None = None
    stock_code: str | None = None
    retrieved_at: str = ""
    verification_status: str = "metadata_verified"
    source_priority: int = 100

    def __post_init__(self) -> None:
        self.title = str(self.title or "").strip()
        self.url = str(self.url or "").strip()
        self.source = str(self.source or "").strip()
        self.source_type = str(self.source_type or "").strip()
        self.summary = str(self.summary or "").strip()
        self.document_type = str(self.document_type or "").strip()
        self.issuer = str(self.issuer).strip() if self.issuer else None
        self.stock_code = str(self.stock_code).strip() if self.stock_code else None
        self.published_at = str(self.published_at).strip() if self.published_at else None
        self.retrieved_at = str(self.retrieved_at or utc_now_iso()).strip()
        if self.verification_status not in VERIFICATION_STATUSES:
            self.verification_status = "rejected"
        try:
            self.source_priority = int(self.source_priority)
        except (TypeError, ValueError):
            self.source_priority = 100

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Evidence":
        allowed = {field.name for field in fields(cls)}
        return cls(**{key: item for key, item in value.items() if key in allowed})
