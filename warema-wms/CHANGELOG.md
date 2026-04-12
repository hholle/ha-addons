# Changelog

## 1.1.1

- Fix position inversion: setting 25% no longer jumps to 75%. Bridge now handles the WMS↔HA position mapping explicitly (WMS 0=open, HA 100=open) instead of relying on `position_open`/`position_closed` discovery fields.
- Fix type 2A (Lamellendach) erratic behaviour: stale `blindMoveToPos` commands are now removed from the queue before a new one is enqueued, so only the latest command reaches the motor.
- Reduce `blindMoveToPos` retry from 3 to 1 to prevent old commands from blocking the serial queue.

## 1.1.0

- Node.js upgraded from 18 to 24.
- Type 2A (Lamellendach): no longer polled (`noPoll` flag); OPEN maps to ang=100, CLOSE to ang=-60; tilt snaps to discrete steps (-60, -33, 0, 33, 66, 100); optimistic state update.
- Debounce (700 ms) on `set_position` and `set_tilt` to absorb rapid HA slider drags.
- Stale-device watchdog: devices that miss 3 consecutive polls are marked offline and removed from the poll list; auto-recover after 1 minute.
- Reduce `blindGetPos` retries from 5 to 2 to prevent radio channel saturation.
- `device_groups` config option: group multiple SNRs under one HA device (e.g. all Terrassendach motors in one device).

## 1.0.0

- Initial release
- Support for WMS device types: 06 (weather), 20 (plug receiver), 21 (actuator UP), 25 (vertical awning), 2A (Lamellendach)
- MQTT auto-discovery for Home Assistant
- Configurable polling and moving intervals
- `force_devices` option to skip scan and register devices directly
- Re-publishes discovery payloads when Home Assistant restarts
- ESM module format (Node.js 18+)
