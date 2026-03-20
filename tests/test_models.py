"""Tests for the EarnEP1Device model."""

from __future__ import annotations

from earn_e_p1.models import EarnEP1Device


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
