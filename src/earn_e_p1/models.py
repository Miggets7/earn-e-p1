"""Data models for EARN-E P1 devices."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EarnEP1Device:
    """Represents an EARN-E P1 meter's accumulated state."""

    host: str
    serial: str | None = None
    model: str | None = None
    sw_version: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
