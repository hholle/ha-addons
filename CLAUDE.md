# CLAUDE.md

Guidance for Claude Code working in this repository.

## Repository

Henrik Holle's private HA add-on repository. All add-ons: `amd64` only, local source builds.
HA instance: `http://192.168.178.201:8123/`
Install repo in HA: Settings → Add-ons → Add-on Store → ⋮ → Repositories → `https://github.com/hholle/ha-addons`

| Add-on | Purpose | Hardware |
|---|---|---|
| **vito-optolink** | Viessmann Vito heating → MQTT (direct P300) | USB optolink `/dev/ttyUSBVito` |
| **electricity-meter** | Electricity meter → MQTT | USB optical adapter `/dev/ttyUSBZaehler` |
| **kaco-powador** | KACO Powador solar inverter → MQTT | USB-RS232 adapter `/dev/ttyUSBPV` |
| **warema-wms** | Warema WMS blinds/covers → MQTT | WMS USB stick `/dev/ttyUSB0` |

## Release process

1. Edit code
2. Bump `version` in `config.yaml`
3. Update `CHANGELOG.md`
4. Push to `main`
5. HA: Settings → Add-ons → [addon] → Rebuild

**Critical**: `image:` in `config.yaml` must be **commented out** for local/source builds. HA Supervisor uses the `version` field to detect that a rebuild is needed. If you add a new schema field, bump version — HA strips unknown fields until it reloads the schema.

## Architecture

### vito-optolink — Python
```
run.sh          # bashio config → env vars → exec python3 -m app.main
app/
  main.py       # poll loop (fast/slow datapoints), modes: run | discover | codings
  p300.py       # P300 driver (serial 4800 baud 8E2)
  datapoints.py / codings.py / derived.py / timers.py   # datapoint decoding, derived values (burner stats)
  discover.py   # address range scan; mqtt_bridge.py: HA discovery + state topics
datapoints/20CB.yaml, 20CB_codings.yaml   # datapoint definitions for device 20CB
tests/          # python3 -m unittest discover -s . -t .
deploy.sh       # tests, then commit+push (git) or copy to HA over SSH (local)
```

### electricity-meter / kaco-powador — Python
```
run.sh          # bashio config → env vars → exec Python
zaehler.py / wechselrichter.py (kaco-powador)
```

### warema-wms — Python
```
run.sh          # bashio config → env vars → exec python3 -m app.main
app/
  wms.py        # WMS protocol (encode/decode) + serial driver: command queue, retries, polling
  main.py       # bridge: stick ↔ MQTT ↔ HA discovery, commands, watchdog
tests/          # python3 -I -m unittest discover -s . -t .  (needs pyserial, paho-mqtt)
```

All add-ons publish HA MQTT discovery payloads (retained) at startup → entities auto-created, no manual YAML.

## vito-optolink specifics

- Replaces the removed `vcontrold` add-on: same device identifier (`vcontrold`) and `unique_id`s, so existing entities and dashboards keep working
- Device ID `20CB` = Vitotronic 200 (VScotHO1), P300 protocol; datapoints live in `datapoints/20CB.yaml` (entries with `verified: false` are logged at startup)
- Controller values are little-endian; states under `vito_optolink/<id>`, availability under `vito_optolink/status`
- `PLAN.md` documents the design and the findings from the old add-on

## Python add-ons specifics

- All use `paho-mqtt>=2.0` with `CallbackAPIVersion.VERSION2`
- **electricity-meter**: IEC 62056-21 D0 — sends `/?!\r\n`, reads until `!`, parses OBIS with regex. 300 baud 7E1. Manual regex parsing (no `iec62056` lib) for ACE3000 compatibility.
- **kaco-powador**: inverter pushes lines continuously; bridge reads and publishes all fields. Betriebszustand numeric codes mapped to strings.

## warema-wms specifics

### Protocol
- Serial: 125000 baud, `{R06<snr>...}` framing
- **Position**: WMS 0 = open/retracted, 100 = closed/extended. Bridge inverts for HA (HA 100 = open): publishes `100 - wmsPos`, sends `100 - haPos` on set_position. Do **not** use `position_open`/`position_closed` in discovery — handle inversion in code.
- **Angle encoding**: `byte = clamp(percent/100 × 75, −75, 75) + 127`

### Device types
| Type | Device | Notes |
|------|--------|-------|
| `06` | Weather station | Passive broadcast, auto-registered |
| `20` | Plug receiver | position + tilt |
| `21` | Actuator UP | position + tilt |
| `25` | Seitenmarkise | position + state, responds to blindGetPos |
| `2A` | Lamellendach | Command-only, `no_poll`, tilt steps only |

### Type 2A (Lamellendach) — non-obvious behaviour
- No `blindGetPos` support → `no_poll=True` on the blind, or it blocks the serial queue
- OPEN = ang 100, CLOSE = ang −60
- Tilt snaps to discrete steps: `−60, −33, 0, 33, 66, 100`
- Optimistic state: publish tilt immediately since no poll feedback
- **Commissioning required**: stick SNR must be registered as authorised sender in WMS Studio Pro before motor responds

### Reliability settings
- `blindGetPos` retry: 2 (reduced from 5 — radio flooding causes Seitenmarkisen to stop responding)
- `blindMoveToPos` retry: 1 + queue dedup (stale queued moves for same blind removed before new one enqueued)
- Debounce: 700 ms on `set_position` and `set_tilt`
- Watchdog: devices silent for 3× polling interval → marked offline and not polled for 1 min; any answer (poll or command ack) marks them online again
- Timeouts (blindGetPos 500 ms, move 500 ms, stop 200 ms, scan 750 ms) are empirical values from the original npm library; there is no official WMS protocol documentation. A `Timeout …` log line usually means the device is out of radio range. Stick on a USB 2 port / extension cable (USB 3 interference).

### Config options (env vars set by run.sh)
`WMS_SERIAL_PORT`, `WMS_CHANNEL`, `WMS_PAN_ID`, `WMS_KEY`, `IGNORED_DEVICES`, `FORCE_DEVICES`, `DEVICE_GROUPS`, `POLLING_INTERVAL`, `MOVING_INTERVAL`, `LOG_LEVEL`

`device_groups` format: `"Name:snr1,snr2;OtherName:snr3"` — groups SNRs under one HA device via shared `identifiers` array in discovery payload.
