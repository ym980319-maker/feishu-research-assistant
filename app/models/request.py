"""Request models for the standalone research service."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator


class ResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    task_type: str
    query: str

    @field_validator("task_type", "query")
    @classmethod
    def require_non_empty_text(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("字段不能为空")
        return normalized
