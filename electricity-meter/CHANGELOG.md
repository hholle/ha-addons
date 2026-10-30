<!-- https://developers.home-assistant.io/docs/add-ons/presentation#keeping-a-changelog -->

## 1.2.0

- Verify the telegram's BCC checksum; corrupted telegrams are discarded instead of published
- New diagnostic entity "Zähler-ID" (OBIS C.1)
- New option `poll_interval` (seconds between request starts, default 60); the interval now includes the read time

- Remove the dead entities of earlier versions (Leistung Bezug/Einspeisung, Bezug Tarif 1/2) from HA by clearing their retained discovery configs
- Values are published as plain numbers (no leading zeros); readings with an unexpected unit are skipped
- Modernised for current Home Assistant: removed deprecated `build.yaml` (base image now set in the Dockerfile), added `DOCS.md` and option translations (en, de)

## 1.1.1

- Availability topic + MQTT last-will: entities become unavailable instead of showing stale values
- Marked offline after 3 consecutive failed polls or on serial error
- MQTT connect retried at startup instead of crashing
- Discovery re-published on every MQTT (re)connect
- Non-numeric OBIS values are skipped
- Fixed misplaced "discovery published" log line
- Removed unused `iec62056` dependency

## 1.1.0

- Full rewrite: long-running process instead of cron
- IEC 62056-21 D0 mode: proper trigger + full telegram reading (instead of single-line scan)
- MQTT connection established once at startup
- MQTT Discovery: entities auto-created in HA on startup
- Additional OBIS codes: 1.8.0 (import), 2.8.0 (export)
- Missing OBIS codes silently skipped — works regardless of which codes the meter sends
- Graceful serial error handling with automatic reconnect
- Converted config.json → config.yaml, build.json → build.yaml
- Updated base image to ghcr.io/home-assistant/amd64-base-python:3.11
- Restricted to amd64 architecture
- Device path configurable via add-on options

## 1.0.22

- Initial release
