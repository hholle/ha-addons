# Home Assistant Add-on: KACO Powador

Connects a KACO Powador solar inverter to Home Assistant via MQTT using the RS232 serial interface.

## Requirements

- KACO Powador inverter with RS232 output connected via USB-serial adapter
- Udev rule renaming the adapter to `/dev/ttyUSBPV` (or configure a different path in options)
- Mosquitto MQTT broker add-on installed and running

## Configuration

| Option | Default | Description |
|---|---|---|
| `device` | `/dev/ttyUSBPV` | Path to the USB-serial device |

## MQTT topics

All sensors are published under `wechselrichter/<name>` with `retain: true`.
Entities are auto-created in HA via MQTT Discovery — no manual configuration needed.

| Topic | Description | Unit |
|---|---|---|
| `wechselrichter/einspeisung` | Eingespeiste Leistung (AC feed-in) | W |
| `wechselrichter/temperatur` | Gerätetemperatur | °C |
| `wechselrichter/generator_spannung` | Generator Spannung (DC input) | V |
| `wechselrichter/generator_strom` | Generator Strom (DC current) | A |
| `wechselrichter/netz_spannung` | Netzspannung (AC grid voltage) | V |
| `wechselrichter/netz_strom` | Netzstrom (AC grid current) | A |
| `wechselrichter/betriebszustand` | Betriebszustand | text |

## Betriebszustand values

| Value | Meaning |
|---|---|
| Nacht | Night / sleep mode |
| Warten | Waiting (sunrise) |
| Einspeisen | Feeding in (producing) |
| Abschaltung (Netz) | Grid shutdown |
| Störung | Fault |
| Fehler | Error |
