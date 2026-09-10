"""Data models for EARN-E P1 devices."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class PacketType(StrEnum):
    """Type of UDP packet broadcast by an EARN-E P1 meter."""

    REALTIME = "realtime"
    TELEGRAM = "telegram"


@dataclass
class EarnEP1Device:
    """Represents an EARN-E P1 meter's accumulated state."""

    host: str
    serial: str | None = None
    model: str | None = None
    sw_version: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    seen_packet_types: set[PacketType] = field(default_factory=set)

    @property
    def data_complete(self) -> bool:
        """Return True once a packet of every type has been seen.

        Each packet type always carries every key the meter supports, so once
        all types have arrived, `data` holds this meter's complete key set and
        any key still absent is one the meter does not report.
        """
        return self.seen_packet_types.issuperset(PacketType)
