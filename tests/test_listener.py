"""Tests for the EarnEP1Listener and standalone discover/validate functions."""

from __future__ import annotations

import asyncio
import json
import random
import socket
from unittest.mock import MagicMock

import pytest

from earn_e_p1.listener import EarnEP1Listener, discover, validate
from earn_e_p1.models import PacketType


# --- Real captured telegrams ---
#
# The payloads below are verbatim captures from EARN-E hardware, kept exactly
# as they came off the wire. They document the two-packet shape the listener
# relies on: a realtime packet keyed by `power_delivered`, and a telegram
# ("heartbeat") packet keyed by `energy_delivered_tariff1` that also carries
# `gas_delivered` when a gas meter is attached. See `_PACKET_TYPE_KEYS` in
# `earn_e_p1.listener`.

# Meter B48A0AD0E0A6 — single phase, no gas meter.
REAL_SINGLE_PHASE_REALTIME = b'{"timestamp":"2025-08-05T07:23:17Z","serial":"B48A0AD0E0A6","power_delivered":0.005,"power_returned":0,"voltage_l1":222,"current_l1":0}'
REAL_SINGLE_PHASE_TELEGRAM = b'{"timestamp":"2025-08-05T08:45:02Z","swVersion":233,"serial":"B48A0AD0E0A6","equipment_id":"4530303638303031303132333638353230","model":"CTA5ZIV-METER","smr":22,"wifiRSSI":-55,"energy_delivered_tariff1":386.4289856,"energy_delivered_tariff2":0,"energy_returned_tariff1":1.700999975,"energy_returned_tariff2":0}'

# Meter CCBA97F52084 — three phase, Belgian meter, no gas meter. The `model`
# value contains a literal backslash, so the byte literal is raw to keep the
# JSON escape (`\\`) intact on the wire.
REAL_THREE_PHASE_REALTIME = b'{"timestamp":"2025-08-05T08:49:12Z","serial":"CCBA97F52084","power_delivered":0,"power_returned":0,"voltage_l1":223.3999939,"voltage_l2":223.5,"voltage_l3":223.3999939,"current_l1":0,"current_l2":0,"current_l3":0}'
REAL_THREE_PHASE_TELEGRAM = rb'{"timestamp":"2025-08-05T08:07:01Z","swVersion":238,"serial":"CCBA97F52084","equipment_id":"3153414733313030323239333638","model":"FLU5\\253769484_A","smr":22,"wifiRSSI":-56,"energy_delivered_tariff1":363.2260132,"energy_delivered_tariff2":204.0099945,"energy_returned_tariff1":0.028000001,"energy_returned_tariff2":0.481000006}'

# Meter EC64C9C0D674 — telegram from a meter with a gas meter attached. No
# matching realtime capture exists for this meter, so it is tested on its own.
# Newer firmware (swVersion 228) omits the `smr` key the 2025 captures carry.
REAL_GAS_TELEGRAM = b'{"timestamp":"2026-02-19T08:17:34Z","swVersion":228,"serial":"EC64C9C0D674","equipment_id":"4530303839303031303232323237353234","model":"CTA5ZIV-METER","wifiRSSI":-61,"energy_delivered_tariff1":1006.36499,"energy_delivered_tariff2":1111.404053,"energy_returned_tariff1":0,"energy_returned_tariff2":0,"gas_delivered":782.9940186}'


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


# --- Packet type tracking ---


async def test_realtime_packet_records_packet_type(
    listener: EarnEP1Listener,
) -> None:
    received = asyncio.Event()
    device_ref = None

    def callback(device, raw):
        nonlocal device_ref
        device_ref = device
        received.set()

    listener.register("127.0.0.1", callback)
    await listener.start()

    await _send_packets(
        [b'{"power_delivered": 1.5, "voltage_l1": 230.0}'],
        listener.port,
    )

    async with asyncio.timeout(2):
        await received.wait()

    await listener.stop()

    assert device_ref.seen_packet_types == {PacketType.REALTIME}
    assert device_ref.data_complete is False


async def test_realtime_then_telegram_completes_data(
    listener: EarnEP1Listener,
) -> None:
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

    assert device_ref.seen_packet_types == {
        PacketType.REALTIME,
        PacketType.TELEGRAM,
    }
    assert device_ref.data_complete is True


async def test_packet_without_witness_keys_records_no_type(
    listener: EarnEP1Listener,
) -> None:
    # Identified by `serial`, but carries neither witness key, so it tells us
    # nothing about which packet type it is.
    received = asyncio.Event()
    device_ref = None

    def callback(device, raw):
        nonlocal device_ref
        device_ref = device
        received.set()

    listener.register("127.0.0.1", callback)
    await listener.start()

    await _send_packets(
        [json.dumps({"serial": "S1", "model": "v1"}).encode()],
        listener.port,
    )

    async with asyncio.timeout(2):
        await received.wait()

    await listener.stop()

    assert device_ref.seen_packet_types == set()
    assert device_ref.data_complete is False


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


async def test_standalone_validate_partial_then_full_returns_serial(
    random_port: int,
) -> None:
    # A partial packet (no serial) arrives first, then a full packet with the
    # serial. validate() must wait for the serial and not resolve early.
    task = asyncio.create_task(
        _send_packets(
            [
                b'{"power_delivered": 1.5}',
                b'{"serial": "S1", "power_delivered": 1.5}',
            ],
            random_port,
            delay=0.1,
        )
    )
    device = await validate("127.0.0.1", timeout=2, port=random_port)
    await task

    assert device is not None
    assert device.serial == "S1"


async def test_standalone_validate_partial_only_times_out(
    random_port: int,
) -> None:
    # Only partial packets (no serial) arrive from the target host, so
    # validate() must time out and return None.
    task = asyncio.create_task(
        _send_packets(
            [b'{"power_delivered": 1.5}'] * 3,
            random_port,
            delay=0.1,
        )
    )
    device = await validate("127.0.0.1", timeout=0.5, port=random_port)
    await task

    assert device is None


async def test_standalone_validate_full_first_returns_serial(
    random_port: int,
) -> None:
    # A full packet with the serial arrives first: unchanged behavior, returns
    # the device with the serial populated.
    task = asyncio.create_task(
        _send_packets(
            [b'{"serial": "S1", "power_delivered": 1.5}'] * 3,
            random_port,
            delay=0.1,
        )
    )
    device = await validate("127.0.0.1", timeout=2, port=random_port)
    await task

    assert device is not None
    assert device.serial == "S1"


# --- Regression tests against real captured telegrams ---


async def test_real_single_phase_no_gas(listener: EarnEP1Listener) -> None:
    """Pin down a single-phase meter with no gas meter attached.

    Real capture from meter B48A0AD0E0A6. The realtime packet reports only L1,
    and neither packet carries `gas_delivered`, so those keys stay absent from
    `data` even though the device is complete.
    """
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
        [REAL_SINGLE_PHASE_REALTIME, REAL_SINGLE_PHASE_TELEGRAM],
        listener.port,
    )

    async with asyncio.timeout(2):
        await received.wait()

    await listener.stop()

    assert device_ref.seen_packet_types == {
        PacketType.REALTIME,
        PacketType.TELEGRAM,
    }
    assert device_ref.data_complete is True
    assert device_ref.serial == "B48A0AD0E0A6"
    assert "voltage_l1" in device_ref.data
    assert "current_l1" in device_ref.data
    for absent in (
        "voltage_l2",
        "voltage_l3",
        "current_l2",
        "current_l3",
        "gas_delivered",
    ):
        assert absent not in device_ref.data


async def test_real_three_phase_no_gas(listener: EarnEP1Listener) -> None:
    """Pin down a three-phase Belgian meter with no gas meter attached.

    Real capture from meter CCBA97F52084. All three phases are reported, gas
    is not, and the Belgian `model` value carries a literal backslash that has
    to survive JSON parsing.
    """
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
        [REAL_THREE_PHASE_REALTIME, REAL_THREE_PHASE_TELEGRAM],
        listener.port,
    )

    async with asyncio.timeout(2):
        await received.wait()

    await listener.stop()

    assert device_ref.data_complete is True
    assert device_ref.serial == "CCBA97F52084"
    for phase_key in (
        "voltage_l1",
        "voltage_l2",
        "voltage_l3",
        "current_l1",
        "current_l2",
        "current_l3",
    ):
        assert phase_key in device_ref.data
    assert "gas_delivered" not in device_ref.data
    assert device_ref.model == r"FLU5\253769484_A"


async def test_real_telegram_carries_gas(listener: EarnEP1Listener) -> None:
    """Pin down that gas readings arrive in the telegram packet.

    Real capture from meter EC64C9C0D674, which has a gas meter attached.
    `gas_delivered` ships in the same packet as the four energy totals, which
    is why `_PACKET_TYPE_KEYS` can key `PacketType.TELEGRAM` on
    `energy_delivered_tariff1` and still expect gas to be there. No realtime
    packet was captured for this meter, so `data_complete` stays False.
    """
    received = asyncio.Event()
    device_ref = None

    def callback(device, raw):
        nonlocal device_ref
        device_ref = device
        received.set()

    listener.register("127.0.0.1", callback)
    await listener.start()

    await _send_packets([REAL_GAS_TELEGRAM], listener.port)

    async with asyncio.timeout(2):
        await received.wait()

    await listener.stop()

    assert device_ref.seen_packet_types == {PacketType.TELEGRAM}
    assert device_ref.data_complete is False
    assert device_ref.serial == "EC64C9C0D674"
    assert device_ref.data["gas_delivered"] == 782.9940186
    for energy_key in (
        "energy_delivered_tariff1",
        "energy_delivered_tariff2",
        "energy_returned_tariff1",
        "energy_returned_tariff2",
    ):
        assert energy_key in device_ref.data
