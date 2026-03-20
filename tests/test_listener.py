"""Tests for the EarnEP1Listener and standalone discover/validate functions."""

from __future__ import annotations

import asyncio
import json
import random
import socket
from unittest.mock import MagicMock

import pytest

from earn_e_p1.listener import EarnEP1Listener, discover, validate


@pytest.fixture
def listener() -> EarnEP1Listener:
    """Create a listener on a random free port."""
    return EarnEP1Listener(port=0)


@pytest.fixture
def random_port() -> int:
    """Return a random high port for standalone function tests."""
    return random.randint(50000, 60000)


async def _send_packets(
    data: list[bytes],
    port: int,
    delay: float = 0.05,
) -> None:
    """Helper to send UDP packets to a port."""
    transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
        asyncio.DatagramProtocol,
        family=socket.AF_INET,
    )
    try:
        for packet in data:
            transport.sendto(packet, ("127.0.0.1", port))
            await asyncio.sleep(delay)
    finally:
        transport.close()


# --- Start / Stop lifecycle ---


async def test_start_stop(listener: EarnEP1Listener) -> None:
    await listener.start()
    assert listener.is_running
    await listener.stop()
    assert not listener.is_running


async def test_stop_when_not_started(listener: EarnEP1Listener) -> None:
    await listener.stop()  # should not raise


# --- Register / Unregister ---


async def test_register_unregister(listener: EarnEP1Listener) -> None:
    callback = MagicMock()
    listener.register("192.168.1.100", callback)
    listener.unregister("192.168.1.100")


async def test_unregister_unknown_host(listener: EarnEP1Listener) -> None:
    listener.unregister("10.0.0.1")  # should not raise


# --- Receiving packets ---


async def test_receive_valid_packet(listener: EarnEP1Listener) -> None:
    received = asyncio.Event()
    results: list = []

    def callback(device, raw):
        results.append((device, raw))
        received.set()

    listener.register("127.0.0.1", callback)
    await listener.start()

    await _send_packets([b'{"power_delivered": 1.5}'], listener.port)

    async with asyncio.timeout(2):
        await received.wait()

    await listener.stop()

    assert len(results) == 1
    device, raw = results[0]
    assert device.host == "127.0.0.1"
    assert device.data == {"power_delivered": 1.5}
    assert raw == {"power_delivered": 1.5}


async def test_packet_from_unregistered_ip_ignored(
    listener: EarnEP1Listener,
) -> None:
    callback = MagicMock()
    listener.register("10.0.0.1", callback)  # different from 127.0.0.1
    await listener.start()

    await _send_packets([b'{"power_delivered": 1.5}'], listener.port)
    await asyncio.sleep(0.1)

    await listener.stop()
    callback.assert_not_called()


# --- Packet parsing edge cases ---


async def test_invalid_json_ignored(listener: EarnEP1Listener) -> None:
    callback = MagicMock()
    listener.register("127.0.0.1", callback)
    await listener.start()

    await _send_packets([b"not json"], listener.port)
    await asyncio.sleep(0.1)

    await listener.stop()
    callback.assert_not_called()


async def test_non_dict_payload_ignored(listener: EarnEP1Listener) -> None:
    callback = MagicMock()
    listener.register("127.0.0.1", callback)
    await listener.start()

    await _send_packets([b"[1, 2, 3]"], listener.port)
    await asyncio.sleep(0.1)

    await listener.stop()
    callback.assert_not_called()


async def test_packet_without_identify_keys_ignored(
    listener: EarnEP1Listener,
) -> None:
    callback = MagicMock()
    listener.register("127.0.0.1", callback)
    await listener.start()

    await _send_packets([b'{"unrelated_key": 42}'], listener.port)
    await asyncio.sleep(0.1)

    await listener.stop()
    callback.assert_not_called()


# --- Device info extraction ---


async def test_serial_set_once(listener: EarnEP1Listener) -> None:
    received = asyncio.Event()
    call_count = 0
    device_ref = None

    def callback(device, raw):
        nonlocal call_count, device_ref
        call_count += 1
        device_ref = device
        if call_count == 2:
            received.set()

    listener.register("127.0.0.1", callback)
    await listener.start()

    await _send_packets(
        [
            b'{"serial": "FIRST", "power_delivered": 1.0}',
            b'{"serial": "SECOND", "power_delivered": 2.0}',
        ],
        listener.port,
    )

    async with asyncio.timeout(2):
        await received.wait()

    await listener.stop()
    assert device_ref.serial == "FIRST"


async def test_model_and_sw_version_updated(listener: EarnEP1Listener) -> None:
    received = asyncio.Event()
    call_count = 0
    device_ref = None

    def callback(device, raw):
        nonlocal call_count, device_ref
        call_count += 1
        device_ref = device
        if call_count == 2:
            received.set()

    listener.register("127.0.0.1", callback)
    await listener.start()

    await _send_packets(
        [
            json.dumps({"serial": "S1", "model": "v1", "swVersion": "1.0"}).encode(),
            json.dumps({"serial": "S1", "model": "v2", "swVersion": "2.0"}).encode(),
        ],
        listener.port,
    )

    async with asyncio.timeout(2):
        await received.wait()

    await listener.stop()
    assert device_ref.model == "v2"
    assert device_ref.sw_version == "2.0"


# --- Data merging ---


async def test_data_merging_across_packets(listener: EarnEP1Listener) -> None:
    received = asyncio.Event()
    call_count = 0
    device_ref = None

    def callback(device, raw):
        nonlocal call_count, device_ref
        call_count += 1
        device_ref = device
        if call_count == 2:
            received.set()

    listener.register("127.0.0.1", callback)
    await listener.start()

    await _send_packets(
        [
            b'{"power_delivered": 1.5, "voltage_l1": 230.0}',
            b'{"serial": "S1", "energy_delivered_tariff1": 100.0}',
        ],
        listener.port,
    )

    async with asyncio.timeout(2):
        await received.wait()

    await listener.stop()

    assert device_ref.data["power_delivered"] == 1.5
    assert device_ref.data["voltage_l1"] == 230.0
    assert device_ref.data["energy_delivered_tariff1"] == 100.0


# --- Multiple devices ---


async def test_multiple_devices_independent(listener: EarnEP1Listener) -> None:
    received = asyncio.Event()
    results_1: list = []
    results_2: list = []

    def cb1(device, raw):
        results_1.append(device)
        received.set()

    def cb2(device, raw):
        results_2.append(device)

    listener.register("127.0.0.1", cb1)
    listener.register("10.0.0.1", cb2)
    await listener.start()

    await _send_packets([b'{"power_delivered": 1.5}'], listener.port)

    async with asyncio.timeout(2):
        await received.wait()

    await listener.stop()

    assert len(results_1) == 1
    assert len(results_2) == 0


# --- Instance discover() ---


async def test_instance_discover_while_running(
    listener: EarnEP1Listener,
) -> None:
    callback = MagicMock()
    listener.register("127.0.0.1", callback)
    await listener.start()

    task = asyncio.create_task(
        _send_packets(
            [b'{"serial": "S1", "power_delivered": 1.5}'] * 3,
            listener.port,
        )
    )
    devices = await listener.discover(timeout=0.5)
    await task
    await listener.stop()

    assert len(devices) == 1
    assert devices[0].serial == "S1"
    assert callback.call_count > 0


# --- Instance validate() ---


async def test_instance_validate_while_running(
    listener: EarnEP1Listener,
) -> None:
    callback = MagicMock()
    listener.register("127.0.0.1", callback)
    await listener.start()

    task = asyncio.create_task(
        _send_packets(
            [b'{"serial": "S1", "power_delivered": 1.5}'] * 3,
            listener.port,
        )
    )
    device = await listener.validate("127.0.0.1", timeout=2)
    await task
    await listener.stop()

    assert device is not None
    assert device.serial == "S1"


# --- Standalone discover() ---


async def test_standalone_discover_finds_device(random_port: int) -> None:
    task = asyncio.create_task(
        _send_packets(
            [b'{"serial": "S1", "power_delivered": 1.5}'] * 5,
            random_port,
            delay=0.1,
        )
    )
    devices = await discover(timeout=1, port=random_port)
    await task

    assert len(devices) == 1
    assert devices[0].host == "127.0.0.1"
    assert devices[0].serial == "S1"


async def test_standalone_discover_no_devices(random_port: int) -> None:
    devices = await discover(timeout=0.3, port=random_port)
    assert devices == []


# --- Standalone validate() ---


async def test_standalone_validate_success(random_port: int) -> None:
    task = asyncio.create_task(
        _send_packets(
            [b'{"serial": "S1", "power_delivered": 1.5}'] * 5,
            random_port,
            delay=0.1,
        )
    )
    device = await validate("127.0.0.1", timeout=2, port=random_port)
    await task

    assert device is not None
    assert device.host == "127.0.0.1"
    assert device.serial == "S1"


async def test_standalone_validate_timeout(random_port: int) -> None:
    device = await validate("192.168.1.100", timeout=0.3, port=random_port)
    assert device is None


async def test_standalone_validate_wrong_host_ignored(random_port: int) -> None:
    task = asyncio.create_task(
        _send_packets(
            [b'{"power_delivered": 1.5}'] * 3,
            random_port,
            delay=0.1,
        )
    )
    device = await validate("10.0.0.1", timeout=0.5, port=random_port)
    await task

    assert device is None
