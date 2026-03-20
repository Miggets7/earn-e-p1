"""UDP listener for EARN-E P1 devices."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable

from .const import DEFAULT_PORT
from .models import EarnEP1Device

_LOGGER = logging.getLogger(__name__)

Callback = Callable[[EarnEP1Device, dict[str, Any]], None]

_IDENTIFY_KEYS = {"power_delivered", "serial"}


class _EarnEP1Protocol(asyncio.DatagramProtocol):
    """UDP protocol that receives and parses EARN-E P1 packets."""

    def __init__(self, listener: EarnEP1Listener) -> None:
        self._listener = listener

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


class EarnEP1Listener:
    """UDP listener that demultiplexes EARN-E P1 packets by source IP."""

    def __init__(self, port: int = DEFAULT_PORT) -> None:
        self._port = port
        self._devices: dict[str, tuple[EarnEP1Device, Callback]] = {}
        self._transport: asyncio.DatagramTransport | None = None
        self._discover_state: dict[str, EarnEP1Device] | None = None
        self._validate_state: (
            tuple[str, EarnEP1Device, asyncio.Future[EarnEP1Device]] | None
        ) = None

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
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _EarnEP1Protocol(self),
            local_addr=("0.0.0.0", self._port),
            allow_broadcast=True,
        )
        self._transport = transport
        _LOGGER.debug("UDP listener started on port %s", self.port)

    async def stop(self) -> None:
        """Close the socket and stop listening."""
        if self._transport:
            self._transport.close()
            self._transport = None
            _LOGGER.debug("UDP listener stopped")

    async def discover(self, timeout: int | float = 10) -> list[EarnEP1Device]:
        """Discover devices using the active socket.

        Temporarily accepts packets from all IPs for the timeout duration.
        Normal registered callbacks continue to fire.
        """
        discovered: dict[str, EarnEP1Device] = {}
        self._discover_state = discovered
        try:
            await asyncio.sleep(timeout)
        finally:
            self._discover_state = None
        return list(discovered.values())

    async def validate(
        self, host: str, timeout: int | float = 65
    ) -> EarnEP1Device | None:
        """Validate a host using the active socket.

        Temporarily accepts packets from the specified host.
        Normal registered callbacks continue to fire.
        """
        found: asyncio.Future[EarnEP1Device] = (
            asyncio.get_running_loop().create_future()
        )
        device = EarnEP1Device(host=host)
        self._validate_state = (host, device, found)
        try:
            async with asyncio.timeout(timeout):
                return await found
        except TimeoutError:
            return None
        finally:
            self._validate_state = None

    def _handle_packet(self, source_ip: str, payload: dict[str, Any]) -> None:
        """Dispatch a parsed packet to registered devices and discover/validate."""
        # Normal registered device handling
        entry = self._devices.get(source_ip)
        if entry is not None:
            device, callback = entry
            _update_device(device, payload)
            callback(device, payload)

        # Instance discover — accept from any IP
        if self._discover_state is not None:
            if source_ip not in self._discover_state:
                self._discover_state[source_ip] = EarnEP1Device(host=source_ip)
            _update_device(self._discover_state[source_ip], payload)

        # Instance validate — accept from specific host
        if self._validate_state is not None:
            v_host, v_device, v_future = self._validate_state
            if source_ip == v_host and not v_future.done():
                _update_device(v_device, payload)
                v_future.set_result(v_device)


def _update_device(device: EarnEP1Device, payload: dict[str, Any]) -> None:
    """Update device state from a parsed packet."""
    if "serial" in payload and device.serial is None:
        device.serial = payload["serial"]
    if "model" in payload:
        device.model = payload["model"]
    if "swVersion" in payload:
        device.sw_version = str(payload["swVersion"])
    device.data.update(payload)


async def discover(
    timeout: int | float = 10,
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
    timeout: int | float = 65,
    port: int = DEFAULT_PORT,
) -> EarnEP1Device | None:
    """Validate that a specific host is an EARN-E device.

    Returns as soon as the first valid packet arrives from the target host,
    or None on timeout. Raises OSError if the port can't be bound.
    """
    listener = EarnEP1Listener(port=port)
    await listener.start()
    try:
        return await listener.validate(host=host, timeout=timeout)
    finally:
        await listener.stop()
