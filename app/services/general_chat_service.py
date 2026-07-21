"""General research-assistant chat handler."""

from __future__ import annotations

from collections.abc import Awaitable, Callable


ModelHandler = Callable[[str, str], Awaitable[str]]


async def handle_general_chat(
    message: str,
    model_handler: ModelHandler,
) -> str:
    return await model_handler(message, "普通问答")

