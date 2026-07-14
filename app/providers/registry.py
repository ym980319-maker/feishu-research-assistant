from __future__ import annotations

from .base import EvidenceProvider, env_bool
from .official import CninfoProvider, MiitProvider


def official_research_enabled() -> bool:
    return env_bool("OFFICIAL_RESEARCH_ENABLED", False)


def get_enabled_providers() -> list[EvidenceProvider]:
    if not official_research_enabled():
        return []
    providers: list[EvidenceProvider] = []
    if env_bool("OFFICIAL_CNINFO_ENABLED", True):
        providers.append(CninfoProvider())
    if env_bool("OFFICIAL_MIIT_ENABLED", True):
        providers.append(MiitProvider())
    return providers

