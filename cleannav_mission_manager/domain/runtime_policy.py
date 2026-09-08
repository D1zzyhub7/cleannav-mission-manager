"""Shared runtime admission policy for Mission Manager composition."""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from cleannav_mission_manager.domain.command_processing import CommandSource


VOICE_MIN_CONFIDENCE = 0.80
"""Minimum confidence admitted for commands whose source is VOICE."""


MIN_CONFIDENCE_BY_SOURCE: Mapping[int, float] = MappingProxyType({
    int(CommandSource.VOICE): VOICE_MIN_CONFIDENCE,
})
"""Frozen source-to-threshold policy shared by mock and real runtimes."""


__all__ = [
    'MIN_CONFIDENCE_BY_SOURCE',
    'VOICE_MIN_CONFIDENCE',
]
