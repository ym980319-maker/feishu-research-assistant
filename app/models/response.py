"""Response models for the standalone research service."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ResearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = "ok"
    task_type: str
    query: str
    research_task_type: str | None = None
    content: str
