"""Data models for parsed ima knowledge-base responses."""

from dataclasses import dataclass


@dataclass(frozen=True)
class KnowledgeBaseInfo:
    """Basic metadata for an ima knowledge base."""

    knowledge_base_id: str
    name: str
    description: str
    total_size: int
    update_time: int
    member_count: int
    access_status: int | None


@dataclass(frozen=True)
class ImaItem:
    """A normalized item returned by an ima knowledge-base listing."""

    media_id: str
    title: str
    parent_folder_id: str
    media_type: int
    media_type_name: str
    create_time: int
    update_time: int
    abstract: str
    introduction: str
    file_size: int
    parse_progress: int
    raw_file_url: str
    parsed_file_url: str
    md5_sum: str
    is_top: bool
    folder_id: str | None
    file_number: int | None
    folder_number: int | None


@dataclass(frozen=True)
class ImaPage:
    """A normalized page of ima knowledge-base items."""

    knowledge_base: KnowledgeBaseInfo
    items: list[ImaItem]
    next_cursor: str
    total_size: int
    version: str
    is_end: bool


__all__ = [
    "KnowledgeBaseInfo",
    "ImaItem",
    "ImaPage",
]
