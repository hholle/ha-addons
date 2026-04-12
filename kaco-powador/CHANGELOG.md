<!-- https://developers.home-assistant.io/docs/add-ons/presentation#keeping-a-changelog -->

## 1.1.0

- Full rewrite: long-running process instead of cron
- MQTT connection established once at startup (not per line)
- MQTT Discovery: all 7 entities auto-created in HA on startup
- All available fields now published (generator voltage/current, grid voltage/current, operating state, feed-in power, temperature)
- KACO Betriebszustand codes mapped to human-readable German text
- Graceful serial error handling with automatic reconnect
- Converted config.json → config.yaml, build.json → build.yaml
- Updated base image to ghcr.io/home-assistant/amd64-base-python:3.11
- Restricted to amd64 architecture
- Device path configurable via add-on options

## 1.0.22

- Initial release
