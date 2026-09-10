"""Tests for the EarnEP1Device model."""

from __future__ import annotations

from earn_e_p1.models import EarnEP1Device, PacketType


def test_device_defaults() -> None:
    device = EarnEP1Device(host="192.168.1.100")
    assert device.host == "192.168.1.100"
    assert device.serial is None
    assert device.model is None
    assert device.sw_version is None
    assert device.data == {}


def test_device_with_all_fields() -> None:
    device = EarnEP1Device(
        host="192.168.1.100",
        serial="ABC123",
        model="P1-Monitor",
        sw_version="1.2.3",
        data={"power_delivered": 1.5},
    )
    assert device.serial == "ABC123"
    assert device.model == "P1-Monitor"
    assert device.sw_version == "1.2.3"
    assert device.data == {"power_delivered": 1.5}


def test_fresh_device_has_no_packet_types() -> None:
    device = EarnEP1Device(host="192.168.1.100")
    assert device.seen_packet_types == set()
    assert device.data_complete is False


def test_data_incomplete_with_only_realtime() -> None:
    device = EarnEP1Device(
        host="192.168.1.100",
        seen_packet_types={PacketType.REALTIME},
    )
    assert device.data_complete is False


def test_data_incomplete_with_only_telegram() -> None:
    device = EarnEP1Device(
        host="192.168.1.100",
        seen_packet_types={PacketType.TELEGRAM},
    )
    assert device.data_complete is False


def test_data_complete_with_both_packet_types() -> None:
    device = EarnEP1Device(
        host="192.168.1.100",
        seen_packet_types={PacketType.REALTIME, PacketType.TELEGRAM},
    )
    assert device.data_complete is True
