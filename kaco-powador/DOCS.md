# Home Assistant Add-on: KACO Powador

Reads a KACO Powador solar inverter over RS232 (USB-RS232 adapter). The inverter pushes one line of values continuously; the add-on publishes every field to Home Assistant via MQTT with auto-discovery.

## Requirements

- USB-RS232 adapter connected to the inverter, available as `/dev/ttyUSBPV` (udev rule) or another configured path
- Mosquitto MQTT broker add-on running

## Options

| Option | Default | Description |
|---|---|---|
| `device` | `/dev/ttyUSBPV` | Serial device of the adapter. If you change it, also change `devices:` in `config.yaml`. |

## Entities

All values are published retained under `wechselrichter/…`; entities are created via MQTT discovery on the device "KACO Powador".

| Entity | Topic | Unit |
|---|---|---|
| Eingespeiste Leistung | `wechselrichter/einspeisung` | W |
| Gerätetemperatur | `wechselrichter/temperatur` | °C |
| Generator Spannung / Strom | `wechselrichter/generator_spannung` / `_strom` | V / A |
| Netzspannung / Netzstrom | `wechselrichter/netz_spannung` / `_strom` | V / A |
| Betriebszustand | `wechselrichter/betriebszustand` | text |

Operating state codes 0–5 are mapped to German text (Nacht, Warten, Einspeisen, Abschaltung (Netz), Störung, Fehler).

## Reliability

- Lines with missing or non-numeric measurement fields are skipped.
- Entities become *unavailable* after 5 minutes without serial data, on a serial error, and when the add-on stops (MQTT last-will). Expect this at night if the inverter powers down.
