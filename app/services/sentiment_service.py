"""Sentiment-analysis task handler."""

from __future__ import annotations

from collections.abc import Awaitable, Callable


ModelHandler = Callable[[str, str], Awaitable[str]]


async def handle_sentiment_analysis(
    message: str,
    model_handler: ModelHandler,
) -> str:
    return await model_handler(message, "舆情梳理")

