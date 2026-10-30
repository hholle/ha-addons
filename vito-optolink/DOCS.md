# Home Assistant Add-on: Vito Optolink

Talks P300 directly to a Viessmann Vitotronic over a USB Optolink adapter and publishes to Home Assistant via MQTT. Replaces the `vcontrold` add-on (see `PLAN.md`).

Entities use the same `unique_id`s and the same device ("Viessmann Heizung") as the vcontrold add-on, so dashboards keep working. Stop the `vcontrold` add-on before starting this one: only one process can use the serial port.

## Options

| Option | Default | Description |
|---|---|---|
| `device` | `/dev/ttyUSBVito` | Optolink serial device. If you change it, also change `devices:` in `config.yaml`. |
| `poll_fast_s` | `10` | Poll interval for burner, boiler and pump datapoints (seconds). |
| `poll_slow_s` | `60` | Poll interval for all other datapoints (seconds). |
| `takt_warn_starts_h` | `6` | `Takt-Warnung` turns on at this many burner starts within the last hour. |
| `publish_codings` | `true` | Show the coding values of the controller as read-only diagnostic entities (read at start and every 6 hours). |
| `log_level` | `info` | `debug` also logs every byte sent and received. |

## Burner statistics

Besides the controller values the add-on publishes statistics for the short-cycling analysis:

- burner starts today, in the last hour and in 24 h (also split into heating and hot water, by the diverter valve at the start)
- last burner run time and last pause (minutes)
- mean run time per start over 24 h (also split into heating and hot water)
- burner run time today (hours) and burner share of the observed time today (%)
- `Takt-Warnung`: problem sensor, on at `takt_warn_starts_h` starts within one hour

The start count comes from the controller counter and is exact. Run and pause times are measured by polling, so they are accurate to about one poll interval (10 s); runs shorter than that count as a start without a run time. The values are kept in `/data/stats.json`, so a restart keeps them. "Today" follows the time zone of the add-on container.

## Time programs

The add-on reads the heating, hot water and circulation time programs every 30 minutes and shows today's switching times (`Warmwasser-Zeiten heute`: `05:30–08:00, 16:00–21:00`) and whether the hot water program is active right now. The whole week is in the attributes of each sensor. The addresses (0x2000, 0x2100, 0x2200) are the usual ones of this controller family and not confirmed for every device. The add-on uses the time zone of Home Assistant.

## Coding values

With `publish_codings` on, the add-on reads the coding values at start and then every 6 hours and shows them as diagnostic entities (`Codierung 59 Einschaltpunkt Speicher`, ...). The values are the raw numbers of the coding menu. Only the heating circuits that exist are shown, based on code 00. The add-on never writes a coding value.

## Coding dump

Set `mode` to `codings` and start the add-on: it reads all 106 known coding values once (read only) and writes a table with code, address and raw value to the log and to `/share/vito-optolink/codings_<time>.txt`. The values are the numbers of the coding menu, so you can compare them with the display. The address list comes from the ViessData21 datapoint list for VScotHO1 and is not verified for every software version. Set `mode` back to `run` afterwards.

## Discovery mode

Finds out which addresses mean what (e.g. burner modulation, flue gas temperature). Read-only, nothing is written to the controller.

1. Set `mode: discover` (optionally `reference_temps`, e.g. `11.8 41.1` = values currently shown on the display, `scan_ranges`, `scan_rounds`, `scan_interval_s`) and start the add-on.
2. **The burner must start or stop during the run**, otherwise flame-related addresses cannot be told apart. In night/shutdown mode the burner only runs for hot water: either run it during the day in heating mode or draw hot water. Use enough rounds to cover a burner cycle (e.g. 90 rounds x 10 s).
3. The report is logged and written to `/share/vito-optolink/discovery_<time>.txt`. It also lists the operating mode seen during the run.
4. Set `mode: run` again and restart.

## Datapoints

Defined in `datapoints/20CB.yaml` (address, length, type, scaling, group, Home Assistant class). Entries with `verified: false` are logged at startup: check them against the controller display.

MQTT: states under `vito_optolink/<id>`, availability under `vito_optolink/status`.

## Rollback

Stop this add-on and start `vcontrold` again. It republishes its own discovery and state topics.

## Tests

```
python3 -m unittest discover -s . -t .
```
(needs `pyyaml`, `paho-mqtt`, `pyserial`)
