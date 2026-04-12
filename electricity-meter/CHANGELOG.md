<!-- https://developers.home-assistant.io/docs/add-ons/presentation#keeping-a-changelog -->

## 1.1.0

- Full rewrite: long-running process instead of cron
- IEC 62056-21 D0 mode: proper trigger + full telegram reading (instead of single-line scan)
- MQTT connection established once at startup
- MQTT Discovery: entities auto-created in HA on startup
- Additional OBIS codes: 1.7.0 (live power), 2.7.0 (live feed-in), 1.8.1/1.8.2 (tariff registers)
- Missing OBIS codes silently skipped — works regardless of which codes the meter sends
- Graceful serial error handling with automatic reconnect
- Converted config.json → config.yaml, build.json → build.yaml
- Updated base image to ghcr.io/home-assistant/amd64-base-python:3.11
- Restricted to amd64 architecture
- Device path configurable via add-on options

## 1.0.22

- Initial release
