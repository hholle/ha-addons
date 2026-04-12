<!-- https://developers.home-assistant.io/docs/add-ons/presentation#keeping-a-changelog -->

## 1.1.18

- Add MQTT Discovery: all 24 sensors auto-created in HA on startup (no manual sensor config needed)
- Binary sensors for pumps, flame, and fault with correct device_class and German payload values
- Removed unused `dpkg` package from container

## 1.1.17

- Device path configurable via add-on options (no longer hardcoded)
- Added detailed README with MQTT topic reference and udev rule example

## 1.1.16

- Update base image to Alpine 3.21
- Fix vcontrold startup race condition (wait for port 3002 before querying)
- Fix error log accumulation (log via bashio instead of writing files)
- Fix fragile vcontrold kill command
- Remove unused external port 3002 mapping
- Fix stray quote in device path
- Update CI workflows to current HA best practices

## 1.1.14

- Updates

## 1.1.11

- Initial release
