# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
