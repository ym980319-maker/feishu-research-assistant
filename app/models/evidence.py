"""Evidence model used before public information enters an LLM prompt."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Evidence:
    title: str
    content: str
    source: str
    published_time: str = ""
    url: str = ""

