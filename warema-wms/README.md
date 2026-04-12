# Warema WMS

Control Warema WMS blinds, awnings, and patio roofs via a WMS USB stick,
published to MQTT with Home Assistant auto-discovery.

## Supported devices

| WMS Type | Device | HA Entity |
|----------|--------|-----------|
| `06` | Weather station eco | Sensors: illuminance, temperature, wind speed, rain |
| `20` | Plug receiver | Cover with position + tilt |
| `21` | Actuator UP | Cover with position + tilt |
| `25` | Vertical awning / Seitenmarkise | Cover with position |
| `2A` | Lamellendach / Motor Wenden | Cover with tilt (-60 closed → 100 open) |

Remote controls (`07`) and web controllers (`09`) are ignored.

## Requirements

- Warema WMS USB stick connected to the HA host
- Mosquitto broker (or any MQTT broker) add-on running
- Devices already joined to the WMS network
- For type `2A` motors: WMS Studio Pro commissioning required to authorize the stick as a sender

## Configuration

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `wms_serial_port` | yes | `/dev/ttyUSB0` | USB stick device path |
| `wms_key` | yes | — | WMS network encryption key (32 hex chars) |
| `wms_pan_id` | yes | `FFFF` | WMS PAN ID (`FFFF` = discovery mode) |
| `wms_channel` | no | `17` | WMS radio channel |
| `ignored_devices` | no | — | Comma-separated SNRs to exclude |
| `force_devices` | no | — | Comma-separated `SNR:TYPE` pairs to register without scanning |
| `device_groups` | no | — | Group multiple SNRs under one HA device. Format: `"Name:snr1,snr2"`, semicolon-separated for multiple groups |
| `polling_interval` | no | `30000` | Position poll interval in ms |
| `moving_interval` | no | `1000` | Poll interval while a blind is moving (ms) |
| `log_level` | no | `info` | Log level: `error` `warn` `info` `verbose` `debug` `silly` |

### Example configuration

```yaml
wms_serial_port: /dev/ttyUSB0
wms_key: "43F83A46274605B63AA38C9C7A14A342"
wms_pan_id: "3871"
wms_channel: 17
force_devices: "1132530:2A,1152669:25,1195016:25"
device_groups: "Terrassendach:1132530,1152669,1195016"
log_level: info
```

## Network parameter discovery

If you don't know your WMS network parameters, set `wms_pan_id: FFFF` and start
the add-on. Press a button on any WMS remote — the add-on will log the channel,
PAN ID, and key. Then update your configuration with the discovered values.

## MQTT topics

All topics are retained. The bridge publishes HA MQTT discovery payloads on
startup and re-publishes when HA restarts.

| Topic | Direction | Description |
|-------|-----------|-------------|
| `warema/bridge/state` | publish | `online` / `offline` |
| `warema/<snr>/availability` | publish | `online` / `offline` per device |
| `warema/<snr>/position` | publish | Position 0–100 |
| `warema/<snr>/tilt` | publish | Tilt angle |
| `warema/<snr>/state` | publish | `open` / `closed` / `stopped` / `opening` / `closing` |
| `warema/<snr>/set` | subscribe | `OPEN` / `CLOSE` / `STOP` |
| `warema/<snr>/set_position` | subscribe | Position 0–100 |
| `warema/<snr>/set_tilt` | subscribe | Tilt value |

## Credits

The WMS radio communication layer is based on the
[**warema-wms-venetian-blinds**](https://www.npmjs.com/package/warema-wms-venetian-blinds)
npm package (MIT licensed). Many thanks to the original author for reverse-engineering
the Warema WMS protocol and publishing the library openly — this add-on would not
exist without that work.
