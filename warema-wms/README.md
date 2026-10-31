# Home Assistant Add-on: Warema WMS

Controls Warema WMS blinds, awnings and patio roofs through a WMS USB stick and publishes them to Home Assistant via MQTT with auto-discovery.

## Supported devices

| WMS type | Device | Home Assistant entity |
|---|---|---|
| `06` | Weather station eco | Sensors: illuminance, temperature, wind speed, rain (registered automatically) |
| `20` | Plug receiver | Cover with position + tilt |
| `21` | Actuator UP | Cover with position + tilt |
| `25` | Vertical awning / Seitenmarkise | Cover with position |
| `2A` | Lamellendach / Motor Wenden | Cover with tilt only (-60 closed → 100 open) |

Remote controls (`07`) and web controllers (`09`) are ignored.

## Requirements

- Warema WMS USB stick connected to the HA host
- Mosquitto MQTT broker add-on running
- Devices already joined to the WMS network
- For type `2A` motors: register the stick's serial number as an authorised sender in WMS Studio Pro (commissioning), otherwise the motor does not react

## Options

| Option | Default | Description |
|---|---|---|
| `wms_serial_port` | `/dev/ttyUSB0` | USB stick device |
| `wms_key` | – | WMS network key (32 hex characters) |
| `wms_pan_id` | `FFFF` | WMS PAN ID. `FFFF` = network discovery mode (see below) |
| `wms_channel` | `17` | WMS radio channel |
| `ignored_devices` | – | Comma-separated serial numbers to leave out |
| `force_devices` | – | Register devices without a radio scan: `SNR:TYPE,SNR:TYPE`, TYPE in hex (default `25`) |
| `device_groups` | – | Show several serial numbers as one HA device: `Name:snr1,snr2;Other:snr3` |
| `polling_interval` | `30000` | Position poll interval in ms (5000–600000) |
| `moving_interval` | `1000` | Poll interval while a blind is moving, in ms (100–60000) |
| `quality_interval` | `300` | Seconds between link quality requests to the stick (LQI/RSSI per device). `0` = off. See "Radio link diagnostics". |
| `rescan_interval` | `0` | Minutes between scans for new devices. `0` = only at startup and on demand (below). |
| `log_level` | `info` | `error`, `warning`, `info` or `debug` (debug logs every frame) |

Example:

```yaml
wms_serial_port: /dev/ttyUSB0
wms_key: "43F83A46274605B63AA38C9C7A14A342"
wms_pan_id: "3871"
wms_channel: 17
force_devices: "1132530:2A,1152669:25,1195016:25"
device_groups: "Terrassendach:1132530,1152669,1195016"
```

## Radio link diagnostics

The WMS stick does not report a signal strength (RSSI). To still see how well a device is reachable, every polled device (awnings, blinds; not the tilt-only type 2A, which is never polled) gets these diagnostic entities, updated every minute:

| Entity | Meaning |
|---|---|
| Reachability | Share of position polls answered in the last hour (%) |
| Response time | Mean time from sending a poll to the answer (ms) |
| Poll retries (1 h) | Extra attempts needed in the last hour |
| Offline events | How often the device was set offline since the add-on started |
| Last seen | Time of the last answer |

They stay visible while the device itself is offline. With `log_level: info` the log shows every position change, every command from Home Assistant, polls that needed retries and a link summary per device every 5 minutes.

### Ping, identify and signal quality (2.2.0)

- **Ping** (button, diagnostic): a silent radio ping (`5040`); the device does not move. The result is shown in the **Last ping** sensor (`ok (42 ms)` or `no answer`) and in the log.
- **Identify** (button): makes the device wave: a short move or blink.
- **TX/RX link quality (LQI) and signal (RSSI)** sensors: values the stick reports for each device. They appear for a device only after the stick has answered
  once. The WMS USB-Stick (firmware 37605107) supports it; if another stick does not, the add-on logs one warning and stops asking. LQI is about 200–230 on a good link; the RSSI values of the
  two directions have different scales (raw bytes, use them relatively per device).

## Library

The protocol code is a separate, dependency-light Python package, `wmslib`, in this folder (only `pyserial` is needed). It builds and decodes frames and has a driver with a
command queue: `from wmslib import Stick, codec`. The add-on uses it; it can also be used on its own. The protocol is described in `PROTOCOL.md` and `MEMORY_MAP.md`.

## Adding new devices

New devices are found without restarting the add-on: press the **Scan for devices** button (on the "Warema WMS" device) or publish `PRESS` to `warema/bridge/scan`. Devices already registered are left alone. With `rescan_interval` the scan also runs periodically.

## Finding the network parameters

Set `wms_pan_id: FFFF` and start the add-on; the log then lists the steps. In short: put a WMS handheld transmitter into learn mode (hold the learn button in its battery case for about 5 s), and each time its scan stops with a red LED press (A). If the log shows `*** Waving and Hello!`, that device is the stick: press STOP on the handheld. The add-on then logs the channel, PAN ID and key. Enter them in the options and restart. No MQTT connection is made in this mode.

If nothing arrives within a few minutes, keep the remote close to the stick; the WMS handshake is short-range.

## Troubleshooting

- `Timeout blindGetPos …` or `Retry …` in the log usually means the device is out of radio range or the channel is busy. Do not shorten the poll intervals.
- Put the stick on a USB 2 port or behind a USB extension cable, away from USB 3 devices; USB 3 interference can make scans find nothing.
- A scan can miss devices. Use `force_devices` to register known serial numbers directly.
- The timeouts and retry counts used here are empirical (taken over from the original npm library); Warema publishes no protocol documentation.

## Behaviour

- **Position scale:** WMS uses 0 = open, 100 = closed. The add-on inverts it for HA (100 = open) in both directions.
- **Type 2A:** the motor does not answer position polls and only knows the tilt steps -60, -33, 0, 33, 66, 100 (other values are snapped). The state shown in HA is what was last sent.
- **Commands:** slider moves are debounced (0.7 s); a newer target replaces a queued older one for the same device.
- **Polling:** each device is polled every `polling_interval`, and every `moving_interval` while it moves. Retries are kept low because flooding the radio channel makes awnings stop answering.
- **Watchdog:** a device that has not answered for 3 poll cycles is marked offline and left alone for a minute. Any answer brings it back.
- **Discovery:** published retained at startup and again whenever Home Assistant restarts.

## MQTT topics

| Topic | Direction | Description |
|---|---|---|
| `warema/bridge/state` | out | `online` / `offline` (last will) |
| `warema/bridge/scan` | in | `PRESS` starts a scan for new devices |
| `warema/<snr>/availability` | out | `online` / `offline` |
| `warema/<snr>/position` | out | 0–100 (HA scale) |
| `warema/<snr>/tilt` | out | tilt angle |
| `warema/<snr>/state` | out | `open` / `closed` / `stopped` / `opening` / `closing` |
| `warema/<snr>/set` | in | `OPEN` / `CLOSE` / `STOP` |
| `warema/<snr>/set_position` | in | 0–100 |
| `warema/<snr>/set_tilt` | in | tilt angle |

## Credits

The radio protocol follows the [warema-wms-venetian-blinds](https://www.npmjs.com/package/warema-wms-venetian-blinds) npm package (MIT), which reverse-engineered the Warema WMS protocol.
