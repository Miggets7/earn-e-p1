# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 0.4.0 - 2026-11-18

### Added
- `EarnEP1Device.equipment_id` holds the smart meter's equipment ID from the
  telegram's `equipment_id` key, as the hex string the dongle sends. Unlike
  `serial`, which belongs to the dongle, it survives a dongle swap. `None` when
  the meter does not report one.

### Changed
- `validate()` now resolves once the serial is known **and** a telegram packet
  has arrived, so the returned device carries the equipment ID. Realtime
  packets alone no longer resolve it. Telegrams arrive about once a minute,
  so callers that pass a short `timeout` must raise it; the 65 second default
  covers one telegram interval. Applies to both `EarnEP1Listener.validate()`
  and the module-level `validate()`. `discover()` is unchanged.

## 0.3.0 - 2026-09-10

### Added
- `PacketType` enum (`REALTIME`, `TELEGRAM`) naming the two UDP packet types an
  EARN-E P1 meter broadcasts, exported from the package root.
- `EarnEP1Device.seen_packet_types` records which packet types have arrived, and
  `EarnEP1Device.data_complete` returns `True` once a packet of every type has
  been seen. Each packet type always carries every key the meter supports, so
  at that point `data` holds the meter's complete key set. A consumer can then
  tell "not seen yet" from "this meter never reports it": a 1-phase meter never
  sends `voltage_l2`/`voltage_l3`, and an electricity-only meter never sends
  `gas_delivered`. Additive and backwards compatible.

## 0.2.0 - 2026-07-15

### Fixed
- `validate()` no longer returns a device with `serial=None`. It now waits for
  a packet containing the serial before resolving, and still returns `None` on
  timeout. Partial packets (instantaneous values, no serial) that arrive first
  are accumulated but no longer resolve the validation early. This applies to
  both `EarnEP1Listener.validate()` and the module-level `validate()`.
  `discover()` is unchanged — it still collects serial-less devices.

## 0.1.0

### Added
- Initial release: async UDP listener for EARN-E P1 energy meters with
  `discover()` and `validate()` helpers.
