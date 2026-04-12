# Home Assistant Add-on: vcontrold

Connects a Viessmann Vito heating system to Home Assistant via MQTT using the [vcontrold](https://github.com/openv/vcontrold) daemon and an USB optolink adapter.

Tested with: **Vitotronic 200 (VScotHO1, device ID 20CB)** using the P300/KW2 protocol.

## Requirements

- USB optolink adapter connected to the Viessmann heating unit
- Udev rule renaming the adapter to `/dev/ttyUSBVito` (or configure a different path in the add-on options)
- Mosquitto MQTT broker add-on installed and running

## Configuration

| Option | Default | Description |
|---|---|---|
| `device` | `/dev/ttyUSBVito` | Path to the USB optolink device |

If your USB adapter appears under a different path (e.g. `/dev/ttyUSB0`), update this option. Note: you also need to update the `devices:` entry in `config.yaml` if you change the udev symlink name.

## MQTT topics

All sensors are published under `vcontrold/<name>` with `retain: true`.

| Topic | Description | Unit |
|---|---|---|
| `vcontrold/Heating_getVitoBetriebsart` | Heating operating mode | enum |
| `vcontrold/Heating_getVitoTempAussen` | Outside temperature | °C |
| `vcontrold/Heating_getVitoTempKesselIst` | Boiler temperature (actual) | °C |
| `vcontrold/Heating_getVitoTempKesselSoll` | Boiler temperature (setpoint) | °C |
| `vcontrold/Heating_getVitoStatusFlamme` | Burner flame status | an/aus |
| `vcontrold/Heating_getVitoStartsBrenner` | Burner start count | — |
| `vcontrold/Heating_getVitoTempWWIst` | Hot water temperature (actual) | °C |
| `vcontrold/Heating_getVitoTempWWSoll` | Hot water temperature (setpoint) | °C |
| `vcontrold/Heating_getVitoTempSpeicher` | Storage temperature | °C |
| `vcontrold/Heating_getVitoTempWWAuslauf` | Hot water outlet temperature | °C |
| `vcontrold/Heating_getVitoStatusPumpeSpeicher` | Storage pump status | an/aus |
| `vcontrold/Heating_getVitoStatusPumpeIntern` | Internal pump status | enum |
| `vcontrold/Heating_getVitoDrehzahlPumpeIntern` | Internal pump speed | — |
| `vcontrold/Heating_getVitoBetriebsartHK` | Heating circuit operating mode | enum |
| `vcontrold/Heating_getVitoTempVLSoll` | Flow temperature setpoint | °C |
| `vcontrold/Heating_getVitoTempRLIst` | Return temperature (actual) | °C |
| `vcontrold/Heating_getVitoStatusPumpeHK` | Heating circuit pump status | an/aus |
| `vcontrold/Heating_getVitoStatusPumpeZirku` | Circulation pump status | an/aus |
| `vcontrold/Heating_getVitoStatusUmschaltventil` | Diverter valve position | enum |
| `vcontrold/Heating_getVitoKennlinieNeigung` | Heating curve slope | — |
| `vcontrold/Heating_getVitoKennlinieNiveau` | Heating curve level | — |
| `vcontrold/Heating_getVitoTempRaumNorSoll` | Room setpoint (normal) | °C |
| `vcontrold/Heating_getVitoTempRaumRedSoll` | Room setpoint (reduced) | °C |
| `vcontrold/Heating_getVitoStatusStoerung` | Fault status | enum |

Poll interval: every **5 minutes**.

## Udev rule example

To give the optolink adapter a consistent device name, add to `/etc/udev/rules.d/99-usb-serial.rules` on the HA host:

```
SUBSYSTEM=="tty", ATTRS{idVendor}=="<vendor>", ATTRS{idProduct}=="<product>", SYMLINK+="ttyUSBVito"
```

Replace `<vendor>` and `<product>` with the values from `lsusb` for your adapter.
