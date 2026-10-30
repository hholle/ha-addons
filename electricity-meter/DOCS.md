# Home Assistant Add-on: Electricity Meter

Reads an Itron ACE3000 electricity meter over the optical interface (IEC 62056-21 D0, 300 baud 7E1) and publishes the values to Home Assistant via MQTT with auto-discovery.

## Requirements

- Optical USB adapter on the meter's optical interface
- Udev rule renaming the adapter to `/dev/ttyUSBZaehler` (or configure another path)
- Mosquitto MQTT broker add-on running

```
SUBSYSTEM=="tty", ATTRS{idVendor}=="<vendor>", ATTRS{idProduct}=="<product>", SYMLINK+="ttyUSBZaehler"
```

Take `<vendor>` and `<product>` from `lsusb`.

## Options

| Option | Default | Description |
|---|---|---|
| `device` | `/dev/ttyUSBZaehler` | Serial device of the optical adapter. If you change it, also change `devices:` in `config.yaml`. |
| `poll_interval` | `60` | Seconds between the start of two meter requests (30–3600). |

## Entities

All values are published retained; entities are created via MQTT discovery.

| Topic | OBIS | Entity | Unit |
|---|---|---|---|
| `zaehler/bezug` | 1.8.0 | Bezug gesamt | kWh |
| `zaehler/einspeisung` | 2.8.0 | Einspeisung gesamt | kWh |
| `zaehler/id` | C.1 | Zähler-ID (diagnostic) | – |

OBIS codes missing from the meter's telegram are skipped, so an entity stays empty if your meter doesn't send that code. `bezug` and `einspeisung` use `state_class: total_increasing` and can be used in the Energy dashboard.

## Reliability

- The telegram's block check character (BCC) is verified; corrupted telegrams are discarded.
- Entities become *unavailable* after 3 failed polls in a row or on a serial error, and when the add-on stops.
