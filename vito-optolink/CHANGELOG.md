<!-- https://developers.home-assistant.io/docs/apps/presentation#keeping-a-changelog -->

## 0.7.0

- Time programs: the add-on reads the heating, hot water and circulation time programs (0x2000, 0x2100, 0x2200, 7 days of 4 on/off pairs, read every 30 minutes) and shows `Heizen-Zeiten heute`, `Warmwasser-Zeiten heute`, `Zirkulation-Zeiten heute` (the whole week as attributes) and `Warmwasser-Programm aktiv`. The addresses are the usual ones of this controller family and not confirmed for every device
- The add-on now uses the time zone of Home Assistant, so "today" and the daily statistics change at local midnight (`tzdata` added to the image)

## 0.6.3

- `Brenneranteil heute` showed 989 % after an update: the saved state of older versions has on-time but no observed time. Such state now starts the day at 0, and the share is capped at 100 %
- Entities that no longer exist (`Drehzahl Pumpe intern`, `Rücklauftemperatur (ist)` and the eight hand-picked `Codierung ... (roh)` sensors of 0.5.0) are removed from Home Assistant: their retained discovery messages are cleared when the add-on connects

## 0.6.2

- Discovery now sends `suggested_display_precision`: temperatures with one decimal, whole-number values without one (no more `249.0 °C`), durations and shares with fixed decimals. The decimal and thousands separators still follow the Home Assistant language/number format (profile settings)

## 0.6.1

- `Kesseltemperatur (soll)` (0x555A) showed 249 °C when the burner is off: the controller reports a placeholder (249/251) then. New datapoint field `max_value`: larger values are published as "None", so Home Assistant shows the sensor as unknown instead of 249 °C

## 0.6.0

- The coding values are now part of the add-on: they are read at start and then every 6 hours and shown as read-only diagnostic entities (`Codierung 59 Einschaltpunkt Speicher`, ...), raw values as in the coding menu. Nothing is ever written
- Only the heating circuits that exist are published, based on code 00 (`Heizkreis-Warmwasserschema`): scheme 1/2 has no M2, scheme 3/4 has no A1
- New option `publish_codings` (default on) to switch this off
- Removed the eight hand-picked `Codierung ...` sensors of 0.5.0: the generated entities replace them (ids `vito_kodierung_<address>`)
- A refused address is logged as a warning instead of an error

## 0.5.2

- Two test sensors for the burner output, to find out which address is right: `Brennerleistung A38F` (value / 2, from a forum post for VScotHO1) and `Modulationsgrad A305` (ViessData21 list), shown next to `Brennerleistung (Modulation)` (0x55D3)

## 0.5.1

- The controller device ID is now passed to Home Assistant: as `model_id` of the device ("Viessmann Heizung") and as the diagnostic entity `Gerätekennung` (value read from 0x00F8 at start, for example `20CB`)
- New mode `codings`: reads all 106 known coding values once (read only) and writes a table with code, address and raw value to the log and to `/share/vito-optolink/codings_<time>.txt`, followed by the raw bytes of the device identification (device ID, software index, coding plug part number, operating unit software index, coding card)

## 0.5.0

- Address check against the openv wiki and the ViessData21 datapoint list for VScotHO1 (device 20CB):
  - `0x0808` is the flue gas sensor, not the return flow: replaced `Rücklauftemperatur (ist)` by `Abgastemperatur` (0x0816, low-pass value). There is no return flow sensor on this unit
  - `Kesseltemperatur (soll)` (0x555A) is a single byte in °C, no longer read as a 2-byte value
  - New: `Warmwasser Soll (effektiv)` (0x6500), `Warmwasserbereitung Status` (0x650A, raw), `Flow Switch` (0x0883, raw), `Vorlauftemperatur gemischt` (0x081A)
  - New, read only and shown as diagnostic entities: coding values 56, 58, 59, 60, 62, 6D, 6F and 06 (raw). Code NN of coding level 2 lives at 0x67NN (hot water) or 0x57NN (boiler)
- New optional field `category` in the datapoint files, published as `entity_category`

## 0.4.2

- Removed `Drehzahl Pumpe intern` (0x7663): it is a 0/1 pump status, not a speed. Delete the old entity in Home Assistant by hand
- New sensor `Brennerbetriebsstunden` (0x08A7, seconds counter / 3600, unverified)
- `Speichertemperatur` is now polled every fast interval
- Every burner start/stop writes one log line with valve, boiler, storage and modulation values and the last run/pause

## 0.4.1

- `Brenneranteil heute` is now relative to the time the add-on actually observed today, so it is not understated after a mid-day start

## 0.4.0

- Burner statistics for short-cycling analysis, published as extra entities: starts (today, last hour, 24 h, split into heating/hot water), last run time and pause, mean run time per start (24 h, also split), burner run time and share today, and a `Takt-Warnung` problem sensor
- New option `takt_warn_starts_h` (default 6): the warning turns on at this many burner starts within the last hour
- Statistics are kept in `/data/stats.json` (saved every 5 minutes and on shutdown), so a restart does not lose them
- Run and pause times are as exact as the poll interval (10 s); the start count comes from the controller counter and is exact
- `Kesseltemperatur (soll)` (0x555A) marked unverified

## 0.3.4

- Burner flame: 55DE also reports `0F` while the burner runs, now "an"

## 0.3.3

- Burner flame: 55DE also reports `08` while the burner runs (seen in a discovery run), now "an" (was unknown)
- New sensor `Brennerleistung (Modulation)` (0x55D3, 0..99 %)

## 0.3.2

- Discovery: chunks refused as a whole are now probed byte by byte in the first round, so readable bytes inside partly unreadable blocks are no longer missed; later rounds read only the readable addresses (faster). The report lists the address map.

## 0.3.1

- Heating circuit/storage/circulation pump: status `03` (pump runs in night mode, seen in the vcontrold repo) is now "an"
- `Betriebsart Heizkreis` (0x2500) marked unverified: the vcontrold repo lists 0x2500 as "Status Frostwarnung M1"

## 0.3.0

- Discovery mode (`mode: discover`, read-only): scans address ranges repeatedly, notes the flame state and operating mode per round, and writes a report to `/share/vito-optolink/` listing addresses that separate flame ON from OFF, words matching reference temperatures, and everything that changed
- Controller error reports (unknown address) no longer count as link failures and are not retried

## 0.2.0

- Polls 24 datapoints (`datapoints/20CB.yaml`) and publishes them via MQTT discovery, same `unique_id`s and device as the vcontrold add-on
- Fast group (burner, boiler, pumps) every `poll_fast_s`, slow group every `poll_slow_s`
- Availability topic with last-will: entities become unavailable instead of showing stale values
- Heating curve slope and pump speed are now read as 1 byte (vcontrold read 2/4 bytes from a 1-byte answer)
- Datapoints not yet verified against the display are logged at startup (`verified: false`)

## 0.1.1

- Debug log (`log_level: debug`) shows every transmitted and received byte as hex

## 0.1.0

- Initial skeleton: direct P300 driver for the Optolink (no vcontrold), unit-tested against a simulated controller
- Connectivity check only: logs device ID (0x00F8) and outside temperature (0x0800); no MQTT yet
