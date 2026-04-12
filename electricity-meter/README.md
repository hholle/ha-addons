# Home Assistant Add-on: Stromzähler

Reads an Itron ACE3000 electricity meter via optical S0 USB interface (IEC 62056-21 D0 mode) and publishes data to Home Assistant via MQTT with auto-discovery.

## Requirements

- Optical USB adapter connected to the meter's optical interface
- Udev rule renaming the adapter to `/dev/ttyUSBZaehler` (or configure a different path in options)
- Mosquitto MQTT broker add-on installed and running

## Configuration

| Option | Default | Description |
|---|---|---|
| `device` | `/dev/ttyUSBZaehler` | Path to the USB optical interface device |

## MQTT topics

All sensors are published with `retain: true`. Entities are auto-created in HA via MQTT Discovery.

| Topic | OBIS | Description | Unit | HA device_class |
|---|---|---|---|---|
| `zaehler/bezug` | 1.8.0 | Gesamtverbrauch (Bezug) | kWh | energy |
| `zaehler/einspeisung` | 2.8.0 | Gesamteinspeisung | kWh | energy |
| `zaehler/leistung_bezug` | 1.7.0 | Aktuelle Leistung Bezug | kW | power |
| `zaehler/leistung_einspeisung` | 2.7.0 | Aktuelle Leistung Einspeisung | kW | power |
| `zaehler/bezug_t1` | 1.8.1 | Verbrauch Tarif 1 | kWh | energy |
| `zaehler/bezug_t2` | 1.8.2 | Verbrauch Tarif 2 | kWh | energy |

OBIS codes not present in the meter's telegram are silently skipped — the add-on works regardless of which codes your meter supports.

The `bezug` and `einspeisung` (1.8.0 / 2.8.0) sensors with `state_class: total_increasing` are automatically available in HA's **Energy dashboard** as grid consumption and return sources.

## Poll interval

The meter is queried every **55 seconds** (IEC 62056-21 at 300 baud takes ~10-15s per telegram).

## Udev rule example

```
SUBSYSTEM=="tty", ATTRS{idVendor}=="<vendor>", ATTRS{idProduct}=="<product>", SYMLINK+="ttyUSBZaehler"
```

Replace `<vendor>` and `<product>` with the values from `lsusb` for your optical adapter.
