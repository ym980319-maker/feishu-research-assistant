"""Parser for ima knowledge-base home-page responses."""

from collections.abc import Mapping

from app.sources.ima_models import ImaItem, ImaPage, KnowledgeBaseInfo


def _to_int(value: object, default: int = 0) -> int:
    """Safely convert a value to int."""

    if isinstance(value, bool):
        return int(value)

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        return int(value)

    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return default
        try:
            return int(stripped)
        except ValueError:
            try:
                return int(float(stripped))
            except ValueError:
                return default

    return default


def _to_str(value: object) -> str:
    """Safely convert a value to str."""

    if value is None:
        return ""

    if isinstance(value, str):
        return value

    return str(value)


def _to_bool(value: object) -> bool:
    """Safely convert a value to bool."""

    if isinstance(value, bool):
        return value

    if isinstance(value, int):
        return value != 0

    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}

    return False


def _get_mapping(
    payload: Mapping[str, object],
    key: str,
) -> Mapping[str, object] | None:
    """Return a nested mapping when present."""

    value = payload.get(key)
    if isinstance(value, Mapping):
        return value
    return None


def _parse_knowledge_base(
    knowledge_base_info: Mapping[str, object],
) -> KnowledgeBaseInfo:
    """Parse knowledge-base metadata."""

    basic_info = _get_mapping(knowledge_base_info, "basic_info")
    if basic_info is None:
        raise ValueError("ima response missing knowledge_base_info.basic_info")

    member_info = _get_mapping(knowledge_base_info, "member_info") or {}
    permission_info = _get_mapping(knowledge_base_info, "permission_info") or {}

    access_status_value = permission_info.get("access_status")
    access_status = (
        _to_int(access_status_value)
        if access_status_value is not None
        else None
    )

    return KnowledgeBaseInfo(
        knowledge_base_id=_to_str(knowledge_base_info.get("id")),
        name=_to_str(basic_info.get("name")),
        description=_to_str(basic_info.get("description")),
        total_size=_to_int(basic_info.get("knowledge_total_size")),
        update_time=_to_int(basic_info.get("update_timestamp_sec")),
        member_count=_to_int(member_info.get("member_count")),
        access_status=access_status,
    )


def _parse_item(raw_item: Mapping[str, object]) -> ImaItem:
    """Parse a single knowledge-base item."""

    media_type_info = _get_mapping(raw_item, "media_type_info") or {}
    folder_info = _get_mapping(raw_item, "folder_info")

    folder_id: str | None = None
    file_number: int | None = None
    folder_number: int | None = None

    if folder_info is not None:
        folder_id = _to_str(folder_info.get("folder_id")) or None
        file_number = _to_int(folder_info.get("file_number"))
        folder_number = _to_int(folder_info.get("folder_number"))

    return ImaItem(
        media_id=_to_str(raw_item.get("media_id")),
        title=_to_str(raw_item.get("title")),
        parent_folder_id=_to_str(raw_item.get("parent_folder_id")),
        media_type=_to_int(raw_item.get("media_type")),
        media_type_name=_to_str(media_type_info.get("name")),
        create_time=_to_int(raw_item.get("create_time")),
        update_time=_to_int(raw_item.get("update_time")),
        abstract=_to_str(raw_item.get("abstract")),
        introduction=_to_str(raw_item.get("introduction")),
        file_size=_to_int(raw_item.get("file_size")),
        parse_progress=_to_int(raw_item.get("parse_progress")),
        raw_file_url=_to_str(raw_item.get("raw_file_url")),
        parsed_file_url=_to_str(raw_item.get("parsed_file_url")),
        md5_sum=_to_str(raw_item.get("md5_sum")),
        is_top=_to_bool(raw_item.get("is_top")),
        folder_id=folder_id,
        file_number=file_number,
        folder_number=folder_number,
    )


def parse_ima_home_response(
    payload: Mapping[str, object],
) -> ImaPage:
    """Parse the response from ima's get_knowledge_base_home_page API."""

    code = _to_int(payload.get("code"))
    if code != 0:
        msg = _to_str(payload.get("msg"))
        raise ValueError(f"ima response error: code={code}, msg={msg}")

    list_rsp = _get_mapping(payload, "list_rsp")
    if list_rsp is None:
        raise ValueError("ima response missing list_rsp")

    knowledge_base_info = _get_mapping(
        list_rsp,
        "knowledge_base_info",
    )
    if knowledge_base_info is None:
        raise ValueError("ima response missing knowledge_base_info")

    knowledge_base = _parse_knowledge_base(knowledge_base_info)

    raw_items = list_rsp.get("knowledge_list")
    items: list[ImaItem] = []

    if isinstance(raw_items, list):
        for raw_item in raw_items:
            if not isinstance(raw_item, Mapping):
                continue
            items.append(_parse_item(raw_item))

    return ImaPage(
        knowledge_base=knowledge_base,
        items=items,
        next_cursor=_to_str(list_rsp.get("next_cursor")),
        total_size=_to_int(list_rsp.get("total_size")),
        version=_to_str(list_rsp.get("version")),
        is_end=_to_bool(list_rsp.get("is_end")),
    )


__all__ = ["parse_ima_home_response"]
