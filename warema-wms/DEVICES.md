# WAREMA WMS: devices, configuration and addressing

Companion to `PROTOCOL.md`. Sources: the German help and UI texts of WMS studio pro 4.37.1.0 **[studio]**, plugin names in
that installer **[plugin]**, public library and forum **[lib]/[forum]**, this installation **[log]**.
Device parameters are read and written as memory blocks (`PROTOCOL.md` §5). The memory map of the radio motor and of the vertical-awning product
(automation parameters, wind monitoring, sensor assignment, drive log) is in `MEMORY_MAP.md`. The maps of the other device and product families were not
extracted yet; this file lists what the help describes for them.

## 1. Concepts

| Term | Meaning |
|---|---|
| **Network** | channel (11–26, default 17) + PAN ID + 128-bit key. Created by the studio or by a handheld; all devices must carry the same data [studio] |
| **SNR** | unique serial number printed on the label; the radio address of a device [studio] |
| **Transmitter (Sender)** | handheld, wall transmitter, central unit (Zentrale), WebControl; sends commands |
| **Receiver (Empfänger)** | motor, actuator, plug receiver, dimmer; executes commands, **stores its own settings and automations** |
| **Sensor** | weather station, wind/sun/temperature sensors; broadcasts values, is assigned to receivers |
| **Channel / Scene** | a transmitter channel addresses up to 10 receivers **of the same product type**; a scene stores fixed positions for up to 200 receivers of any type |
| **System timer** | one device per network (WebControl, WebControl pro or Zentrale) broadcasts the time; timed functions need it |
| **Away / present** | network-wide status set from the Zentrale or WebControl; every automation can be made dependent on it (receivers from revision 1.10) |
| **Master template** | studio feature: one template creates up to 200 linked receivers of the same type |
| **Package** | several drives and sensors combined: awning packages (up to 2 valance roller blinds and 2 wind sensors), slat-roof packages (2 drives) |

Home Assistant only needs the **receiver** side: commands (move, stop, tilt, wave) and status polls. Everything else is
configured with WMS studio pro or a handheld and then stored inside the receiver.

## 2. Device families (plugin names in the installer)

The studio ships one plugin per device and per product. Meaning of the short names is partly inferred **[?]**.

### Transmitters

`HandHeldTransmitter`, `HandHeldTransmitterBasic`, `HandHeldTransmitterPlus`, `WallMountedTransmitterBasic`,
`WallMountedTransmitterPlus`, `TransmitterUp` (flush-mount transmitter), `CentralTransmitter` (WMS Zentrale), `WebControl`,
`WebControlPro`.

- Handhelds sleep and must be woken by a button; "learn" button in the battery compartment starts a network scan; (+)
  selects a channel; **A key** toggles comfort automations (sun, dusk, temperature) for all or the selected channel;
  **C key** short = call comfort position, **C key > 5 s** = save the current position as comfort position [studio].
- The number of channels, scenes and parameters depends on the transmitter type.
- **WebControl pro** is configured completely from the studio project; browser setup is then not needed. It can be the
  system timer, supports automatic summer/winter time, time zone and latitude/longitude (for sunrise/sunset actions)
  [studio]. WebControl and WebControl pro also expose a cloud/HTTP API that is outside this add-on [forum].

### Receivers (actuators)

- `RadioMotor`, `RadioMotorL60L70` (motors for awnings, shutters, blinds and slat roofs; in this installation types `25`
  and `2A`)
- `ActuatorUp`, `ActuatorSa1Up`, `Actuator24V`, `ActuatorPf`, `ActuatorSmart` (flush-mount and low-voltage actuators)
- `PlugReceiver`, `PlugReceiverAtmel`, `PlugReceiverPower`, `PlugReceiverPowerAtmel` (plug-in receivers)
- `Dimmer`, `Dimmer010V`, `DimmerSmart`, `ValanceRollerBlindSmart`

### Sensors and converters

`WeatherStationEco`, `WeatherStationPlus`, `WindSensor`, `SensorIs3`, `SensorIs3Wind`, `PhotoSensor`, `TemperatureSensor`,
`Splitter`, `SplitterAp`, `ClimatronicConverter`, `KnxConverter`.

- **Splitter** forwards weather data from up to three connected weather stations (master) into further WMS networks; it behaves like a
  sensor and is assigned to receivers the same way. It also has a climabus serial number [studio].
- **climatronic converter** lets a WAREMA climatronic control take over: option "safety functions only by climatronic"
  disables wind/ice monitoring in assigned receivers, option "comfort functions only by climatronic" disables sun/dusk/etc.
  [studio]
- **KNX converter**: bridge to KNX; not documented in the help pages read here [?].
- Weather stations broadcast on their own (`7080`), no polling. Battery temperature sensors add battery and humidity [forum].

### Products (what the receiver drives)

Awning variants (plain, one or two valances, with one or two wind sensors, conservatory, drop-arm, facade, Markisolette,
pergola, sun sail), roller blind, roller shutter, external venetian blind, internal venetian blind, pleated blind (inside),
vertical louvre blind, **vertical awning (Zipscreen)**, window, **slat roof L60 / L70 / L70 tilting motor**, valance, light
switching, load switching, load dimmable, LED dimmable, plug-socket switching, floating output.

The product plugin decides which parameters a receiver has (for example angle only for slat products, valance parameters only with a valance).

## 3. Addressing from software

| Aspect | Value |
|---|---|
| Address | SNR of the receiver, 3 bytes little endian in `{R06<snr>…}` (see `PROTOCOL.md`) |
| Discovery | broadcast scan `7020`; receivers answer `7021` with their device type; sleeping devices must be woken first |
| Identify | wave `7050` (device moves briefly or blinks) |
| Control | move `7070` (position, angle, valances), stop, position poll `8010` |
| Sensors | passive: weather broadcasts `7080`, clock `8020` |
| State | polled with `8010`/`01000005`; sleeping battery/solar receivers may not answer at all [studio] |
| Write configuration | not done by this add-on; configuration frames are unknown (studio only) |

Home Assistant mapping done by the add-on: WMS 0 % = open, 100 % = closed is inverted to HA 100 = open; type `2A` is tilt-only
(positions `-60, -33, 0, 33, 66, 100`), type `25`/`20`/`21` have position (`20`/`21` also tilt).

## 4. Configuration procedures (WMS studio pro)

### Network

1. **Create a new network**: new PAN ID and key, channel 17 by default; a project can hold several networks.
2. **Check radio channel**: measures load per channel, recommends quiet channels, sensitivity slider, CSV export.
3. **Change channel / key**: devices must be reloaded after a change; "replace network key" is for lost transmitters or copied networks
   (existing serial numbers are removed from the copy).
4. **Reset network**: new key, all device data and serial numbers deleted, all devices have to be loaded again.
5. **Duplicate / merge networks and projects**; **read configuration** of a known or unknown network (needs a handheld to
   read the network credentials: "read network access data", pressing A or i, "wave" function).

### Loading a device

1. Wake battery or solar devices (button press) and have the stick attached.
2. Studio scans and lists devices in factory state: sensors, receivers, transmitters.
3. **Identify** the right device by serial number or by waving it.
4. Select the planned device in the workspace and load; green tick = ok, yellow = repeat, red = aborted.
5. Factory reset ("Werkseinstellungen laden"): sensors and actuators back to defaults and removed from the network; transmitters also lose
   assignments.

### Motor setup

- **Final positions (Endlagen)**: 4-step dialog (delete, drive to positions, test, finish); cancelling deletes the positions.
  Status shows whether positions are programmed. Learning modes: final positions bottom→top, top→bottom, runtime bottom→top,
  runtime bottom→turn→top [studio].
- **Motor settings**: functions on/off, read after saving to verify; advanced data read-only and exportable as CSV.
- **Slat roof package**: two drives are combined into one package; **awning package**: drive plus up to two valance roller blinds
  and two wind sensors, with drop setting; both have a "test" tab for radio link and movement [studio].
- **Run times are needed** for comfort position and scene positions to work reliably [studio].

### Basic parameters (receiver, "Basisfunktionen")

Comfort functions on/off; away on/off; run time up/down (also valance 1/2); venetian type; awning type; turning time; min/max angle; turning
steps; calibration up/down (extra run time); motor direction (also valance 1/2); position/angle after a manual down command; hold time after a
manual command; position/angle for status away; comfort position (position, angle, valance, LED brightness, light, load).

### Automations (parameters per receiver, all with "on/off", "settable by transmitter", "when away", "when present")

| Automation | Parameters |
|---|---|
| **Sun** | threshold sun, position/angle/valance sun, delay sun; threshold cloud (must be below sun), position/angle/valance cloud, delay cloud; optional time windows (4 timer points per weekday release or block it) |
| **Dusk** | morning: not before (Mon–Fri / Sat–Sun), threshold, position/angle; evening: not before, threshold, position/angle |
| **Temperature** | max temperature, position, delay; min temperature, position, delay |
| **Wind** | threshold, on delay, off delay, position/angle/valance |
| **Ice** | threshold, position/angle/valance, auto-reset at threshold + 3 K (otherwise only reset from the Zentrale) |
| **Precipitation** | position/angle/valance, off delay, allow manual operation |
| **Timer** | timer points 1–4 (weekdays, time, position/angle/valance, release or block sun/temperature automation) |
| **Scenes** | per scene fixed positions; up to 32 scenes depending on the transmitter |

Safety first: wind, ice and precipitation monitoring have priority; sensor failure shows as "heartbeat" causes in the motor
event log (`PROTOCOL.md` §7).

## 5. What the add-on already covers and what it could add

| Capability | Today |
|---|---|
| Open / close / stop / position / tilt of receivers (types 20, 21, 25, 2A) | yes |
| Position and moving state for polled receivers | yes |
| Slat roof status (type 2A): real tilt and state after commands | yes (2.1.0) |
| Weather station values (type 06) | yes |
| Reachability, retries, response time, offline events per receiver | yes (2.1.0) |
| Wave (identify) as HA button | not yet; `7050` + `50AC` is known |
| Ping, no-sleep, wake-up, channel scan, RSSI | frames unknown, see `PROTOCOL.md` §6 |
| Comfort position / scene call from HA | frames unknown |
| Read motor data / fault causes (motor event log, battery low) | frames unknown, definitions listed in `PROTOCOL.md` §7 |
