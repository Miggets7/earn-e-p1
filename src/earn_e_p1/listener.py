"""UDP listener for EARN-E P1 devices."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

from .const import DEFAULT_PORT
from .models import EarnEP1Device, PacketType

_LOGGER = logging.getLogger(__name__)

Callback = Callable[[EarnEP1Device, dict[str, Any]], None]

_IDENTIFY_KEYS = {"power_delivered", "serial"}

_PACKET_TYPE_KEYS: dict[PacketType, str] = {
    PacketType.REALTIME: "power_delivered",
    PacketType.TELEGRAM: "energy_delivered_tariff1",
}


class _EarnEP1Protocol(asyncio.DatagramProtocol):
    """UDP protocol that receives and parses EARN-E P1 packets."""

    def __init__(self, listener: EarnEP1Listener) -> None:
        self._listener = listener
        self.closed: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        """Handle incoming UDP datagram."""
        source_ip = addr[0]
        try:
            payload = json.loads(data)
        except (json.JSONDecodeError, UnicodeDecodeError):
            _LOGGER.warning("Failed to decode UDP packet from %s", source_ip)
            return

        if not isinstance(payload, dict):
            _LOGGER.warning("Non-dict UDP payload from %s", source_ip)
            return

        if not _IDENTIFY_KEYS.intersection(payload):
            return

        self._listener._handle_packet(source_ip, payload)

    def error_received(self, exc: Exception) -> None:
        """Handle protocol errors."""
        _LOGGER.warning("UDP protocol error: %s", exc)

    def connection_lost(self, exc: Exception | None) -> None:
        """Handle connection lost."""
        if exc:
            _LOGGER.warning("UDP connection lost: %s", exc)
        if not self.closed.done():
            self.closed.set_result(None)


class EarnEP1Listener:
    """UDP listener that demultiplexes EARN-E P1 packets by source IP."""

    def __init__(self, port: int = DEFAULT_PORT) -> None:
        self._port = port
        self._devices: dict[str, tuple[EarnEP1Device, Callback]] = {}
        self._transport: asyncio.DatagramTransport | None = None
        self._protocol: _EarnEP1Protocol | None = None
        # Pending discover() and validate() calls, keyed by the future that
        # ends them, so several can run at once and stop() can end them all.
        self._discoveries: dict[asyncio.Future[None], dict[str, EarnEP1Device]] = {}
        self._validations: dict[
            asyncio.Future[EarnEP1Device | None], tuple[str, EarnEP1Device]
        ] = {}

    @property
    def port(self) -> int:
        """Return the port the listener is bound to."""
        if self._transport is not None:
            sock = self._transport.get_extra_info("socket")
            if sock is not None:
                return sock.getsockname()[1]
        return self._port

    @property
    def is_running(self) -> bool:
        """Return True if the listener is active."""
        return self._transport is not None

    def register(self, host: str, callback: Callback) -> None:
        """Register a device by IP address."""
        device = EarnEP1Device(host=host)
        self._devices[host] = (device, callback)
        _LOGGER.debug("Registered device %s", host)

    def unregister(self, host: str) -> None:
        """Unregister a device by IP address."""
        self._devices.pop(host, None)
        _LOGGER.debug("Unregistered device %s", host)

    async def start(self) -> None:
        """Open UDP socket and start listening."""
        loop = asyncio.get_running_loop()
        transport, protocol = await loop.create_datagram_endpoint(
            lambda: _EarnEP1Protocol(self),
            local_addr=("0.0.0.0", self._port),
            allow_broadcast=True,
        )
        self._transport = transport
        self._protocol = protocol
        _LOGGER.debug("UDP listener started on port %s", self.port)

    async def stop(self) -> None:
        """Close the socket and stop listening.

        Pending discover() and validate() calls return right away, as if they
        had timed out. Returns once the socket is closed, so the port can be
        bound again.
        """
        pending: list[asyncio.Future[Any]] = [*self._discoveries, *self._validations]
        for future in pending:
            if not future.done():
                future.set_result(None)
        if self._transport is None or self._protocol is None:
            return
        protocol = self._protocol
        self._transport.close()
        self._transport = None
        self._protocol = None
        # asyncio closes the socket on a later loop iteration.
        await protocol.closed
        _LOGGER.debug("UDP listener stopped")

    async def discover(self, timeout: float = 10) -> list[EarnEP1Device]:
        """Discover devices using the active socket.

        Temporarily accepts packets from all IPs for the timeout duration,
        or until stop() is called. Normal registered callbacks continue to
        fire, and several calls can run at the same time.
        """
        stopped: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        discovered: dict[str, EarnEP1Device] = {}
        self._discoveries[stopped] = discovered
        try:
            async with asyncio.timeout(timeout):
                await stopped
        except TimeoutError:
            pass
        finally:
            del self._discoveries[stopped]
        return list(discovered.values())

    async def validate(self, host: str, timeout: float = 65) -> EarnEP1Device | None:
        """Validate a host using the active socket.

        Temporarily accepts packets from the specified host and resolves once
        the serial is known and a telegram packet has arrived, so the device
        carries its serial and, if the meter reports one, its equipment ID.
        Returns None on timeout or when stop() is called. Normal registered
        callbacks continue to fire, and several calls can run at the same time.
        """
        found: asyncio.Future[EarnEP1Device | None] = (
            asyncio.get_running_loop().create_future()
        )
        self._validations[found] = (host, EarnEP1Device(host=host))
        try:
            async with asyncio.timeout(timeout):
                return await found
        except TimeoutError:
            return None
        finally:
            del self._validations[found]

    def _handle_packet(self, source_ip: str, payload: dict[str, Any]) -> None:
        """Dispatch a parsed packet to registered devices and discover/validate."""
        # Normal registered device handling
        entry = self._devices.get(source_ip)
        if entry is not None:
            device, callback = entry
            _update_device(device, payload)
            callback(device, payload)

        # Instance discover — accept from any IP
        for discovered in self._discoveries.values():
            if source_ip not in discovered:
                discovered[source_ip] = EarnEP1Device(host=source_ip)
            _update_device(discovered[source_ip], payload)

        # Instance validate — accept from specific host
        for v_future, (v_host, v_device) in self._validations.items():
            if source_ip == v_host and not v_future.done():
                _update_device(v_device, payload)
                # Realtime packets carry the serial but not the equipment ID,
                # which only arrives with the telegram. Resolving earlier would
                # hand callers a device whose equipment ID is not known yet.
                if (
                    v_device.serial is not None
                    and PacketType.TELEGRAM in v_device.seen_packet_types
                ):
                    v_future.set_result(v_device)


def _update_device(device: EarnEP1Device, payload: dict[str, Any]) -> None:
    """Update device state from a parsed packet."""
    device.seen_packet_types.update(
        packet_type for packet_type, key in _PACKET_TYPE_KEYS.items() if key in payload
    )
    if "serial" in payload and device.serial is None:
        device.serial = payload["serial"]
    if "equipment_id" in payload:
        device.equipment_id = payload["equipment_id"]
    if "model" in payload:
        device.model = payload["model"]
    if "swVersion" in payload:
        device.sw_version = str(payload["swVersion"])
    device.data.update(payload)


async def discover(
    timeout: float = 10,
    port: int = DEFAULT_PORT,
) -> list[EarnEP1Device]:
    """Listen for EARN-E devices on the network.

    Opens a temporary UDP socket, collects unique devices (by source IP)
    for `timeout` seconds. Raises OSError if the port can't be bound.
    """
    listener = EarnEP1Listener(port=port)
    await listener.start()
    try:
        return await listener.discover(timeout=timeout)
    finally:
        await listener.stop()


async def validate(
    host: str,
    timeout: float = 65,
    port: int = DEFAULT_PORT,
) -> EarnEP1Device | None:
    """Validate that a specific host is an EARN-E device.

    Waits until the target host has sent its serial and a telegram packet,
    and returns the device with its serial and, if the meter reports one, its
    equipment ID populated, or None on timeout. Realtime packets alone do not
    resolve the validation. Raises OSError if the port can't be bound.
    """
    listener = EarnEP1Listener(port=port)
    await listener.start()
    try:
        return await listener.validate(host=host, timeout=timeout)
    finally:
        await listener.stop()
