# Henrik Holle's Home Assistant Add-ons

Private add-on repository for Home Assistant.

Install by adding this repository in HA:
**Settings → Add-ons → Add-on Store → ⋮ → Repositories**
→ `https://github.com/hholle/ha-addons`

## Add-ons

### [vcontrold](./vcontrold)

Connects a Viessmann Vito heating system to Home Assistant via MQTT using the
[vcontrold](https://github.com/openv/vcontrold) daemon and a USB optolink adapter.
Publishes heating temperatures, pump states, burner stats, and operating modes.

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
