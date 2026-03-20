# earn-e-p1

Async Python library for communicating with [EARN-E](https://earn-e.com) P1 energy meters via UDP.

The EARN-E P1 meter reads a smart meter's P1 port and broadcasts real-time energy data via UDP on the local network. This library listens for those broadcasts and provides parsed device data.

## Installation

```bash
pip install earn-e-p1
```

## Usage

### Persistent listener

For long-running applications (e.g., Home Assistant integrations) that need continuous updates:

```python
import asyncio
from earn_e_p1 import EarnEP1Listener, EarnEP1Device

def on_update(device: EarnEP1Device, raw: dict) -> None:
    print(f"Power: {device.data.get('power_delivered')} kW")
    print(f"Serial: {device.serial}")

async def main() -> None:
    listener = EarnEP1Listener()
    listener.register("192.168.1.100", callback=on_update)
    await listener.start()

    try:
        await asyncio.sleep(3600)  # listen for 1 hour
    finally:
        await listener.stop()

asyncio.run(main())
```

The listener supports multiple devices — call `register()` for each device IP. Packets are demultiplexed by source IP and each device maintains its own merged state.

### Discover devices

Find EARN-E devices on the network:

```python
from earn_e_p1 import discover

devices = await discover(timeout=10)
for device in devices:
    print(f"Found {device.host} (serial: {device.serial})")
```

### Validate a specific host

Check if a specific IP is an EARN-E device:

```python
from earn_e_p1 import validate

device = await validate("192.168.1.100", timeout=10)
if device:
    print(f"Confirmed: {device.serial}")
```

### Discover/validate while a listener is running

If a listener is already active, use the instance methods to avoid port conflicts:

```python
# Discover using the active socket
devices = await listener.discover(timeout=10)

# Validate using the active socket
device = await listener.validate("192.168.1.100", timeout=10)
```

## Data Model

The callback receives two arguments:

- `device` (`EarnEP1Device`) — accumulated device state with merged data from all packets
- `raw` (`dict`) — the raw packet as received

```python
@dataclass
class EarnEP1Device:
    host: str                          # Device IP address
    serial: str | None = None          # Serial number (set once from first full telegram)
    model: str | None = None           # Device model
    sw_version: str | None = None      # Firmware version
    data: dict[str, Any] = field(...)  # Merged sensor data from all packets
```

The device sends two types of UDP broadcasts:

| Type | Keys | Frequency |
|------|------|-----------|
| Realtime | `power_delivered`, `power_returned`, `voltage_l1`, `current_l1` | ~1s |
| Full telegram | `energy_delivered_tariff1/2`, `energy_returned_tariff1/2`, `gas_delivered`, `wifiRSSI`, `serial`, `model`, `swVersion` | ~10s |

The library merges all packets into `device.data`, so it always contains the latest value for every key.

## License

MIT
