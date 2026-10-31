# WAREMA WMS: radio and serial protocol

Reverse-engineered notes for the WMS USB stick as used by this add-on. Nothing here is an official specification.

Sources and confidence tags:

| Tag | Meaning |
|---|---|
| **[log]** | seen in a real log of this add-on (one installation: 2 awnings, 1 slat roof) |
| **[code]** | implemented in `app/wms.py`, covered by tests |
| **[lib]** | public library `warema-wms-venetian-blinds` / fork `thauer/warema-wms-api` (raw files read) |
| **[forum]** | ioBroker forum thread 7336, read through a summarising tool, not re-verified |
| **[official]** | WAREMA web page or manual |
| **[studio]** | the WMS studio pro 4.37.1.0 installer: help text, UI texts, .NET metadata, and the application's own web bundle and plugin parameter lists (read locally, see §11) |
| **[?]** | guess |

`[studio]` facts about frame layouts come from the studio's own message classes and are the most complete source. They were
**not** confirmed on the air unless a **[log]** tag is also present. The studio talks to the newer **WMS Stick Pro**; this
installation uses the old **WMS USB-Stick**, which may not implement every command.

## 1. System facts

| Fact | Value | Source |
|---|---|---|
| Radio band | 2.4 GHz | [official] |
| Encryption | 128 bit AES, done inside the stick; key set with `K` | [official] [studio] |
| Lower layers | "at least based on IEEE 802.15.4", proprietary upper layers | [forum] |
| Channels | 11–26, default 17 | [lib] [studio] |
| Network | channel + 2-byte PAN ID + 128-bit key | [studio] |
| Direction | bidirectional, receivers confirm | [official] |
| Routing | devices forward telegrams (transmission mode "wide") | [official] [studio] |
| Limits | ≤200 receivers per network, 10 receivers of one product type per channel, 4 sensors per receiver, 32 scenes | [studio] |
| Power classes | mains devices listen continuously; **battery and solar devices sleep** and must be woken | [studio] |
| Receiver memory | every receiver has addressable memory **blocks** holding configuration, state, sensor readings and the drive log (see `MEMORY_MAP.md`) | [studio] |

## 2. Serial layer (host ↔ stick)

- USB serial, **125000 baud**, 8N1 **[log]**.
- Frame: ASCII between `{` and `}`. Upper-case command letter = host → stick, lower-case = stick → host **[lib]** **[studio]**.
- Spaces in the studio's notation are only for readability; they are not sent.
- Old stick: **no checksum**. WMS Stick Pro and WebControl pro frames carry a checksum before the `}` (`{<body> <checksum>}`); the
  studio detects the stick type (name `WMS USB-Stick`, `WMS-Stick-Pro`, `WMS-Stick-PBL` = bootloader) **[studio]**.
- Numbers in frames are **hex**; multi-byte values (serial number, PAN ID, key, block address) are **little endian**, which the studio
  writes as "hexInLE".

### Stick commands (complete list known to the studio)

| Code | Frame | Meaning | Answer | Source |
|---|---|---|---|---|
| `G` | `{G}` | get name | `{gWMS USB-Stick}` | [log] [lib] [studio] |
| `V` | `{V}` | get version | `{v37605107   }` | [log] [lib] [studio] |
| `K` | `{K4 01 <key 32 hex>}` = `{K401<key>}` | enable encryption with key #1 (key bytes reversed) | `{a}` | [log] [lib] [studio] |
| `P` | `{P}` | **disable encryption** | `{a}` | [studio] |
| `M%` | `{M%<channel 2 dec><PAN 4 hex LE>}` | tunnel mode: set channel and PAN. Example `{M%173871}` = channel 17, PAN bytes `3871` (PAN 0x7138) | `{a}` | [log] [lib] [studio] |
| `M#` | `{M#<channel><PAN>[x\|W]}` | unicast mode; optional `x` = extended, `W` = weather channel | `{a}` | [studio] |
| `Mi` | `{Mi<channel><PAN>}` | integration mode | `{a}` | [studio] |
| `M<` | `{M<<channel>}` | **sniffer mode** on a channel | `{a}` | [studio] |
| `C` | `{C <channel>}` | set stick channel | `{a}` | [studio] |
| `Yon` / `Yoff` | `{Yon}` `{Yoff}` | **channel performance mode** on/off (after `M<`) | `{a}` | [studio] |
| `Q` | `{Q<snr 6 hex LE>}` | **connection quality** of one device | `{q…}` see §7 | [studio] |
| `R` | `{R…}` | radio report (see §3) | `{a}` then `{r…}` | [log] [lib] [studio] |
| `B` | `{B<type><id> <payload>}` | stick report (the stick's own memory blocks, same type/id layout as radio reports) [?] | `{b…}` | [studio] |
| `a` | `{a}` | acknowledge; also used host → stick after received join/wave requests | – | [code] [lib] |
| `f` | `{f<code>}` | stick failure; code `c` = wrong checksum, `s` = unauthorized | – | [studio] |

`{a}` only says the stick accepted the frame; it does not mean the device received it **[log]**.

Stick answers seen: `{q…}` connection quality, `{yC<ch>:<level>}` performance (§8), `{r…}` radio reports.

## 3. Radio reports: the frame envelope

```
host → radio   {R <key mode 1 digit><transmission mode 1 digit> <snr 3 bytes LE> <type 1 byte><id 1 byte> <payload>}
radio → host   {r <snr 3 bytes LE> <type 1 byte><id 1 byte> <payload>}
```

The two digits after `R` that earlier notes called an "unknown prefix" are **key mode** and **transmission mode** **[studio]**:

| Key mode | Meaning |
|---|---|
| `0` | Key0 (the studio uses it for devices that are not in a network yet, "via Key0"; the community library uses it for everything and works with this installation) |
| `1` | Key1 (network key set with `K401`) |
| `2` | Plain (no encryption) |
| `3` | StackDefault |

| Transmission mode | Meaning |
|---|---|
| `0` | OneHopBC (one-hop broadcast) |
| `1` | P2P (point to point) |
| `4` | WideBC (broadcast through the mesh) |
| `6` | WideP2P (point to point routed through the mesh) |
| `8` | NWK2Child |

Observed in the community library and in this installation **[log] [lib]**: unicast commands `R06…` (key `0`, WideP2P), scan request `R04…`
(key `0`, WideBC), scan response `R01…` (key `0`, P2P), ack `R21…` (plain, P2P). The studio's own default for devices in a network is
`R11…` (Key1, P2P), with 300–500 ms timeout and 3 retries **[studio]**.

### Report types (type + id, shown as 4 hex digits)

| Kind | Name | Direction | Payload | Source |
|---|---|---|---|---|
| `5010` | RemoveDeviceFromPan | host → device | – | [studio] |
| `5018` | AddDeviceToPan | host → device (and handheld join request) | PAN LE (2) + key LE (16) + product type (1) + channel (1) | [studio] [lib] |
| `5040` | **Ping** | host → device | – (answer `50AC`) | [studio] |
| `5060` | SetNewChannel | host → device (and handheld request) | PAN LE (2) + filter (1) + channel (1) + do-beckon (1) | [studio] [lib] |
| `50AC` | NetworkAck | device → host | – | [studio] [lib] |
| `7020` | ScanDevicesRequest | host → all | PAN LE (2) + filter (1) | [code] [lib] [studio] |
| `7021` | ScanDevicesResponse | device → host | PAN LE (2) + device type (1) [+ product metadata, see below] | [code] [studio] |
| `7050` | **Beckon (wave)** | host → device | – (answer `50AC`) | [code] [lib] [studio] |
| `7070` | ManualCmd (move, stop) | host → device | see §4 | [code] [log] [lib] |
| `7071` | ManualCmdResponse | device → host | previous state, see §4 | [code] [log] [lib] |
| `7080` | SensorValuesEvent (weather broadcast) | sensor → all | see §6 | [code] [lib] |
| `7820` / `7821` | ISE2 scan request/response (package members) | | | [studio] |
| `8010` | **MB8 read request** | host → device | block (1) + address LE (2) + size (1) | [log] [lib] [studio] |
| `8011` | MB8 read response | device → host | block (1) + address LE (2) + size (1) + data | [log] [studio] |
| `8020` | **MB8 write request** | host → device | block (1) + address LE (2) + size (1) + data | [studio] |
| `8021` | MB8 write response | device → host | | [studio] |
| `80FE` | MB8 failure | device → host | error code (1) | [studio] |

`7021` with product metadata (long form, for actuators): supported product types (12 bytes), product type, control type, with-blinds flag,
with-runtime flag, test bench id, learning mode **[studio]**.

## 4. Driving commands

| Command | Frame | Notes |
|---|---|---|
| move to position | `{R06<snr>7070 03 <pos> <angle> <valance1> <valance2>}` | the add-on and lib send `FFFF` for the valances = not present |
| stop | `{R06<snr>7070 01 FF FF FF FF 00}` | |
| manual command (button event) | `{R06<snr>7070 <function code> …}` | studio `ControlProduct`: up/stop/down × direct/short/long/double press; the function code depends on the product type |
| answer | `{r<snr>7071 …}` | payload `[0:10]` unknown, `[10:12]` **previous** position, `[12:14]` previous angle, `[14:16]` and `[16:18]` previous valances **[lib] [log]** |

Encodings **[code] [lib] [log]**:

- **Position** byte = percent × 2; `00` = open, `C8` = 100 % closed (WMS scale; HA uses 100 = open).
- **Angle** byte = round(percent / 100 × 75) + 127, range `34`…`CA`; `CA` ≈ +100 %, `52` ≈ −60 %, `FF` = none.
- **Valance** positions use the position encoding; `FF` = none.

## 5. Reading and writing receiver memory (MB8)

All receiver state and configuration is a set of **memory blocks**; the studio calls this protocol "MB8".

- **Read**: `{R06<snr>8010 <block><address LE><size>}`; answer `{r<snr>8011 <block><address LE><size><data>}`.
- **Write**: `{R06<snr>8020 <block><address LE><size><data>}`; answer `8021`, failure `80FE`.
- The position poll the add-on has always used, `8010 01000005`, is simply "read block 1, address 0, 5 bytes": position, angle, valance 1,
  valance 2, moving flag **[log] [studio]**. Block 1 is the receiver's `movementState` block.
- A device type has a **parameter list** that names each value and gives its (block, address, data type). The lists are stored in the studio
  plugins; the radio motor's list is in `MEMORY_MAP.md`.
- Studio terms: PL = parameter list, FQN = a parameter's path such as `/control/noSleepCounter` **[studio]**.

Examples **[log]**: `{r083C1280110100000500FFFFFF00}` = block 1, address 0, 5 bytes: position `00` (open), angle `FF`, valances `FF FF`, moving `00`.
Slat roof just after STOP: `{rF24711801101000005FF96FFFF01}` = position byte `FF`, angle `96` (+31 %), moving `01`.

## 6. Sensor broadcasts and clock

- **`7080` weather broadcast** **[code] [lib]**: payload `[2:4]` wind, `[4:6]`/`[12:14]` illuminance (if `[4:6]` = `00`: `[12:14]`×2, else `[4:6]`×`[12:14]`×2),
  `[16:18]` rain (`C8` = rain), `[18:20]` temperature = value/2 − 35 °C (a forum post says offset 40) **[forum]**. The station repeats each broadcast 2–3 times.
  Battery temperature sensors add battery voltage, humidity and an indoor target **[forum]**.
- Every receiver stores the **last readings of up to 4 sensors** (wind, 4 photo values, dusk, battery state, precipitation, temperatures, safety contact, global radiation,
  humidity, …) with timestamps, and the **time since each sensor was last heard**. Reading them is how the studio's "Messwerte auslesen" works **[studio]**
  (`MEMORY_MAP.md`, blocks 10 and 15).
- **Clock**: a system timer (WebControl, WebControl pro or Zentrale) broadcasts the time as an MB8 write of the clock block
  (`8020 0B 0800 09 <YY MM DD HH MM SS DOW …>`) **[lib] [forum]**.

## 7. Connection quality and RSSI (stick command `Q`)

The studio asks the stick for the radio link quality of **one device**: **[studio]**

```
host → stick   {Q<snr 6 hex LE>}
stick → host   {q<snr 6 hex LE><TX LQI 1><TX RSSI 1><RX LQI 1><RX RSSI 1>}
```

- Four unsigned bytes: LQI and RSSI for the transmit direction (stick → device) and for the receive direction (device → stick).
- Defaults used by the studio: key mode 0, P2P, timeout 500 ms, 3 retries; the studio's "measure average RSSI" repeats the query several times and
  averages. Whether the stick performs a radio exchange for it or reads a neighbour table is not visible.
- **Confirmed on the old WMS USB-Stick (firmware `37605107`) [log]**: the stick answers `{a}` and then `{q…}` within about 20 ms; one request per device is enough.
- Real answers (stick → device is "TX", device → stick is "RX"; all three devices of this installation, awnings and slat roof):

  | Device | Answer | TX LQI | TX RSSI | RX LQI | RX RSSI |
  |---|---|---|---|---|---|
  | 1132530 (2A) | `{qF24711DE1ADD63}` | 222 | 26 | 221 | 99 |
  | 1152669 (25) | `{q9D9611D91ADD5A}` | 217 | 26 | 221 | 90 |
  | 1195016 (25) | `{q083C12E124E46C}` | 225 | 36 | 228 | 108 |

- Scale: LQI values 217–228 (of 255) fit a good link. The two RSSI columns differ systematically (TX 26–36, RX 90–108), so the two directions are probably measured or scaled
  differently; the unit (dBm offset) is not known **[?]**. Use the values relatively, per device and over time.

## 8. Channel performance (stick commands `M<`, `Yon`, `Yoff`)

Channel load measurement, as in the studio tool "Funkkanal überprüfen" **[studio]**:

1. `{M<<channel>}` → `{a}` (sniffer mode on that channel).
2. `{Yon}` → `{a}`; the stick then streams `{yC<channel 2 dec>:<level 1 byte hex>}` for the channel (level = energy/noise level; higher = busier).
3. `{Yoff}` → `{a}`, then back to unicast mode with `{M#<channel><PAN>}`.

The studio averages the values per channel and recommends a quiet channel. This is a **channel** measurement, not a per-device value.

## 9. Ping, wave, wake-up and no-sleep

| Function | Frames | Source |
|---|---|---|
| **Ping** | `{R<k><t><snr>5040}` → stick `{a}`, device `{r<snr>50AC}`; the studio uses 500 ms timeout, 3 retries | [studio] |
| **Beckon / wave** | `{R<k><t><snr>7050}` → `{a}`, device moves briefly or blinks, then `{r<snr>50AC}`; 300 ms timeout, 3 retries | [studio] [lib] [code] |
| **Device meta data ("PL query")** | MB8 reads of the device's parameter list / device-parameter block (block 81: software version, device type, …) | [studio] |
| **No-sleep** | MB8 **write** of block 9, address 19 (`/control/noSleepCounter`), 2 bytes = seconds, little endian | [studio] |
| **Studio "Ping and set no-sleep time"** | `SetNwkConv` (channel, PAN, key) → PL query → write the no-sleep counter; if the device has no such parameter: error `NO_SLEEP_TIMER_NOT_SUPPORTED`, on timeout `DEVICE_NOT_REACHED`; no-sleep defaults 120/180/300 s | [studio] |

Sleeping (battery/solar) receivers only listen for a short window after waking. The no-sleep counter keeps a receiver awake for the given number of
seconds **[?]**. Radio motors with mains power probably listen continuously and do not need it **[?]**.

A wake-up of a sleeping handheld is a button press; for motors the studio help only says to wake battery/solar devices before scanning **[studio]**.

Unverified: whether the add-on's silent device (1152669) sleeps. A working ping (`5040` → `50AC`) would answer that cheaply.

## 10. Device types and installation facts

Device types (byte in `7021` and at block 81 address 43) **[studio]**:

| Byte | Device | Source |
|---|---|---|
| `0x25` (37) | radio motor (vertical awning etc.) | [studio] [log] [lib] |
| `0x2A` (42) | radio motor L60/L70 (slat roof tilting motor) | [studio] [log] |
| `0x06` | weather station | [log] [lib] |
| `0x07` | handheld transmitter | [lib] |
| `0x20`, `0x21` | plug receiver / actuator UP (lib and forum disagree on which) | [code] [lib] [forum] |
| `0x02` | stick/software | [lib] |
| `0x63` | WebControl | [lib] [forum] |

Packages ("ISE": slat roof, awnings with valances and wind sensors) assign **each member its own radio channel** and store the member serial numbers in the
receiver (`MEMORY_MAP.md`, block 36) **[studio]**.

## 11. Motor event log, factory reset and other studio functions [studio]

- Fault causes ("Fahrursachen"): each receiver keeps its **last 20 drive commands** (block 4, 20 bytes each) with cause, action and time. Cause/action codes are listed in the studio texts
  (wind alarm, wind heartbeat, ice, rain, manual, sun, scene, wave, thermal protection, obstacle up/down, **battery voltage low**, positioning error, …).
- Operating mode (block 0, address 0): `0` normal, `1` runtime learning, `2` EUI learning, **`3` RSSI ping-pong**, `4` check mode, `5` double power-up. Writing mode 3 puts the motor in its
  RSSI test mode (what the motor then does on the air is unknown; writing the mode was not tried).
- Reset: `reset` at block 9, address 3; `factoryDefaults` at block 9, address 4.
- Radio settings of a device: PAN ID at block 31 address 16 (2 bytes), radio channel at block 31 address 20 (1 byte) **[studio]**.
- Network change operations (add device to PAN, change channel, change key, remove from PAN) are `5018`, `5060`, `5010` frames and MB8 writes; they are not used by this add-on.

## 12. Timing

| | add-on | library | studio |
|---|---|---|---|
| position poll | 0.5 s, 2 retries, 0.1 s pause | 500 ms, 5 retries | MB8 read: 300 ms, 3 retries (default) |
| move | 0.5 s, 1 retry, 0.3 s | 500 ms, 3 retries | |
| stop | 0.2 s, 3 retries | 200 ms, 3 retries | |
| ping, beckon | – | beckon 500 ms | ping 500 ms, beckon 300 ms, 3 retries |
| connection quality | – | – | 500 ms, 3 retries |

The stick answers `{a}` within about 10–30 ms and a device within about 20–60 ms **[log]**. One command is in flight at a time, so a silent device delays the others by its timeout **[log]**.

## 13. Observations in this installation [log]

- Serial 1195016 (type 25): answers every poll, position updates every second while moving.
- Serial 1132530 (type 2A, Lamellendach): answers move and stop; answered one position poll while moving.
- Serial 1152669 (type 25): at first the stick acked but the device never answered polls or move/stop (one of the two awnings moved). **Later (2.2.0 test, 17:42) it answers every
  poll within 40–56 ms** and its link quality is similar to the others (§7). Cause of the earlier silence unknown (asleep, interference, or a state that was cleared).
- Wave `{R06F247117050}` → `{a}` → `{rF2471150AC2BBE}` (`50AC` with data `2BBE`, 2 bytes, meaning unknown) **[log]**.
- Position answers of awnings carry angle byte `FF` (no angle); the decoder returns `angle: None` for it (earlier versions showed a nonsense tilt of 171 %).
- Device 1195016 loses the first poll attempt about every third poll and answers on the retry (20–60 ms); the others almost never do.

## 14. Open questions

1. Does the old stick implement `Yon`/`Yoff`, `P`, `M<`? (`Q` works.)
2. Scale and units of the RSSI bytes (the two directions differ by about 60–70 counts).
3. Does `5040` ping work? (the wave `7050` is answered by `50AC` with 2 data bytes; ping not tested yet). Why was 1152669 silent earlier and answers now?
4. Meaning of the data types and units in the parameter lists (wind threshold, delays, …).
5. Checksum algorithm of the Stick Pro frames.

## 15. Provenance

- Raw files compared: `thauer/warema-wms-api` `lib/wms-vb-wmsutil.js` and `lib/wms-vb-stick.js`.
- Forum: ioBroker topic 7336, through a summarising tool.
- WMS studio pro 4.37.1.0 installer: unpacked; help pages and UI texts read; .NET assemblies listed with REA; the web bundle (`wms-web-studio.js`) and the plugin parameter lists are stored
  encrypted with a key embedded in the application itself. They were decrypted **locally for analysis of the installation owner's own software and hardware** to document
  interoperability facts (frame layouts, memory map). The decrypted files and the key were kept out of the repository; this document contains only protocol facts.
