# CLAUDE.md

Guidance for Claude Code working in this repository.

## Repository

Henrik Holle's private HA add-on repository. All add-ons: `amd64` only, local source builds.
HA instance: `http://192.168.178.201:8123/`
Install repo in HA: Settings → Add-ons → Add-on Store → ⋮ → Repositories → `https://github.com/hholle/ha-addons`

| Add-on | Purpose | Hardware |
|---|---|---|
| **vcontrold** | Viessmann Vito heating → MQTT | USB optolink `/dev/ttyUSBVito` |
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

### vcontrold — Alpine + s6-overlay (shell)
```
rootfs/
  etc/services.d/vcontrold/run     # s6 entrypoint
  usr/bin/run-vito                 # poll loop → heizung.sh every 5 min
  usr/bin/publish-discovery        # MQTT discovery on startup
  etc/vcontrold/
    vcontrold.xml.gtpl             # tempio template → rendered to vcontrold.xml at startup
    heizung.sh / heizung.cmd / heizung.tpl / vito.xml
```

### electricity-meter / kaco-powador — Python
```
run.sh          # bashio config → env vars → exec Python
zaehler.py / kaco-powador.py
```

### warema-wms — Node.js 24 + s6-overlay
```
etc/services.d/warema-wms/run   # s6 entrypoint (note: etc/ not rootfs/)
srv/
  index.js                      # bridge: WMS stick ↔ MQTT ↔ HA discovery
  logger.js
  warema-wms-venetian-blinds/   # WMS protocol library: index.js, stick.js, wms-util.js
test-cli.js                     # test CLI: WMS_SERIAL_PORT/PAN_ID/KEY/CHANNEL node test-cli.js
```

All add-ons publish HA MQTT discovery payloads (retained) at startup → entities auto-created, no manual YAML.

## vcontrold specifics

- `vcontrold` binary built from source in Dockerfile (v0.98.12, cmake, Alpine musl — no committed binaries)
- `heizung.tpl`: `$RN` prefix for enum-typed results (returns text e.g. `"an"`/`"aus"`), `$N` for numeric — intentional vclient template syntax
- Device ID `20CB` = Vitotronic 200 (VScotHO1), P300/KW2 protocol
- `tempio` renders `vcontrold.xml.gtpl` → `vcontrold.xml` at s6 startup

## Python add-ons specifics

- Both use `paho-mqtt>=2.0` with `CallbackAPIVersion.VERSION1`
- **electricity-meter**: IEC 62056-21 D0 — sends `/?!\r\n`, reads until `!`, parses OBIS with regex. 300 baud 7E1. `iec62056` lib is installed but manual parsing used for ACE3000 compatibility.
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
| `2A` | Lamellendach | Command-only, `noPoll` flag, tilt steps only |

### Type 2A (Lamellendach) — non-obvious behaviour
- No `blindGetPos` support → `noPoll = true` on the blind object, or it blocks the serial queue
- OPEN = ang 100, CLOSE = ang −60
- Tilt snaps to discrete steps: `−60, −33, 0, 33, 66, 100`
- Optimistic state: publish tilt immediately since no poll feedback
- **Commissioning required**: stick SNR must be registered as authorised sender in WMS Studio Pro before motor responds

### Reliability settings
- `blindGetPos` retry: 2 (reduced from 5 — radio flooding causes Seitenmarkisen to stop responding)
- `blindMoveToPos` retry: 1 + queue dedup (stale queued moves for same blind removed before new one enqueued)
- Debounce: 700 ms on `set_position` and `set_tilt`
- Watchdog: devices missing 3× polling interval → marked offline, re-added after 1 min quiet period

### Config options (env vars in bridge)
`WMS_SERIAL_PORT`, `WMS_CHANNEL`, `WMS_PAN_ID`, `WMS_KEY`, `IGNORED_DEVICES`, `FORCE_DEVICES`, `DEVICE_GROUPS`, `POLLING_INTERVAL`, `MOVING_INTERVAL`, `LOG_LEVEL`

`device_groups` format: `"Name:snr1,snr2;OtherName:snr3"` — groups SNRs under one HA device via shared `identifiers` array in discovery payload.
