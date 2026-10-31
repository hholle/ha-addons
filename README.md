# Henrik Holle's Home Assistant Add-ons

Private add-on repository for Home Assistant.

Install by adding this repository in HA:
**Settings → Add-ons → Add-on Store → ⋮ → Repositories**
→ `https://github.com/hholle/ha-addons`

## Add-ons

### [vito-optolink](./vito-optolink)

Connects a Viessmann Vitotronic heating controller to Home Assistant via MQTT. Talks the
P300 protocol directly over a USB Optolink adapter and publishes temperatures, pump states,
burner statistics and operating modes with auto-discovery.

Hardware: USB optolink adapter → `/dev/ttyUSBVito`

---

### [electricity-meter](./electricity-meter)

Reads an Itron ACE3000 electricity meter via IEC 62056-21 D0 optical interface and
publishes consumption/feed-in to Home Assistant via MQTT. Supports the HA Energy dashboard.

Hardware: optical USB adapter → `/dev/ttyUSBZaehler`

---

### [kaco-powador](./kaco-powador)

Connects a KACO Powador solar inverter to Home Assistant via MQTT using the RS232 serial interface.

Hardware: USB-RS232 adapter → `/dev/ttyUSBPV`

---

### [warema-wms](./warema-wms)

Controls Warema WMS blinds, awnings, and patio roofs via a WMS USB stick,
published to MQTT with Home Assistant auto-discovery.
Supports covers with position, tilt, weather station sensors, and device grouping.

Hardware: Warema WMS USB stick → `/dev/ttyUSB0`
