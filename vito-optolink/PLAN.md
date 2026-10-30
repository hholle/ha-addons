# Plan: vito-optolink (Viessmann Optolink → Home Assistant)

Neues Add-on im Ordner `ha-addons/vito-optolink`. Das bestehende Add-on `vcontrold` bleibt unverändert und dient als Rückfallpunkt, bis das neue sich bewährt hat.

## 1. Ziele

1. Heizung (Vitotronic 200, Geräte-ID `20CB`) als **ein Gerät** in HA mit allen Sensoren, per MQTT Discovery.
2. **Direkte P300-Kommunikation in Python**, ohne vcontrold und ohne XML-Dateien.
3. **Takten sichtbar machen:** schnelle Abfrage der Brennerdaten, abgeleitete Sensoren (Laufzeit pro Start, Starts pro Stunde).
4. **Adressproblem lösen:** ein Entdeckungsmodus (nur Lesen), der Adressen begründet vorschlägt und in eine YAML-Datei schreibt.
5. **Schreiben optional:** Sollwerte und Betriebsart nur für ausdrücklich freigegebene Datenpunkte.

Nicht-Ziele: Firmware-Updates, Schreiben auf nicht freigegebene Adressen, Cloud-Anbindung.

## 2. Rahmenbedingungen (aus `ha-addons/CLAUDE.md`)

- Nur `amd64`, lokaler Build, `image:` in `config.yaml` bleibt auskommentiert.
- Bei Änderungen: `version` in `config.yaml` erhöhen, `CHANGELOG.md` pflegen, nach `main` pushen, in HA "Rebuild".
- Muster der Python-Add-ons (`electricity-meter`, `kaco-powador`): `run.sh` liest Optionen per bashio, setzt Umgebungsvariablen und startet das Python-Programm. `paho-mqtt>=2.0`, Discovery-Payloads retained beim Start.
- Serielles Gerät: `/dev/ttyUSBVito` (udev-Symlink).

## 3. Prüfung des bestehenden Add-ons `vcontrold` (Version 1.1.18)

Geprüft wurden alle Dateien im Ordner. Zum Stand der Home-Assistant-Vorgaben habe ich per Websuche recherchiert. Wo ich etwas nicht belegen konnte, steht es dabei.

### Funktioniert und passt

| Punkt | Bewertung |
|---|---|
| `services.d` mit `run` und `finish`, Skripte per `chmod` ausführbar | Das alte Layout wird laut [s6-overlay-Hinweisen](https://developers.home-assistant.io/blog/2022/05/12/s6-overlay-base-images/) weiter unterstützt. Ausführbare Rechte sind Pflicht und gesetzt. |
| `finish`-Skript nutzt `/run/s6/basedir/bin/halt` | Entspricht der Empfehlung aus demselben Blogpost. |
| `services: mqtt:need` und `bashio::services mqtt` | Korrekter Weg, um Broker-Zugangsdaten zu bekommen. |
| `tempio`-Template für die Konfiguration | Üblich und korrekt. |
| MQTT Discovery mit gemeinsamem `device` | Gute Praxis, wird übernommen. |
| Einheiten `T1U`, `TD`, `FLS`, `HS` usw. in `vcontrold.xml.gtpl` | Vorhanden, meine frühere Sorge hat sich erledigt. |

### Zu beheben oder zu prüfen

| # | Befund | Schwere | Empfehlung |
|---|---|---|---|
| 1 | **`build.yaml` ist laut [offizieller Doku](https://developers.home-assistant.io/docs/apps/configuration/) veraltet.** Laut [Migrationsbeitrag](https://developers.home-assistant.io/blog/2026/04/02/builder-migration/) nutzt der neue Build-Ablauf sie nicht mehr. Das Basis-Image gehört in die Dockerfile. Berichte aus der Community zeigen leeres `BUILD_FROM`, wenn kein Default gesetzt ist. | mittel | Im neuen Add-on `ARG BUILD_FROM=ghcr.io/home-assistant/base:3.21` mit Default in der Dockerfile, keine `build.yaml`. Ob der Supervisor auf deiner Instanz bei lokalen Builds noch `build.yaml` liest, konnte ich nicht belegen. |
| 2 | **Zustände sind `retain` ohne Verfügbarkeitsanzeige.** Fällt die Abfrage aus, zeigt HA ewig den letzten Wert, als wäre er aktuell. | hoch | `availability_topic` plus Last-Will im neuen Add-on. |
| 3 | **Abfrage nur alle 5 Minuten, vcontrold wird pro Zyklus gestartet und per `pkill` beendet.** Ein Brennerzyklus beim Takten ist kürzer. | hoch (für unser Ziel) | Dauerhafter Dienst mit schneller Abfrage. |
| 4 | **Gerätepfad doppelt:** `devices:` in `config.yaml` ist fest `/dev/ttyUSBVito`, die Option `device` ist änderbar. Ändert man die Option, ohne `devices:` anzupassen, hat der Container keinen Zugriff. Fehlt der udev-Symlink, startet das Add-on nicht. | niedrig | Im neuen Add-on `uart: true` und `devices:` bewusst setzen und dokumentieren. |
| 5 | **`apparmor: false`** schaltet das AppArmor-Profil ab. | niedrig | Für ein privates Add-on vertretbar. Im neuen Add-on prüfen, ob es ohne geht. |
| 6 | **`init: false`.** Für Add-ons auf den HA-Basis-Images mit s6-overlay ist das nach meinem Wissen das vorgesehene Muster. In der Websuche fand ich dazu widersprüchliche Community-Hinweise. | niedrig | Nicht anfassen, solange das Add-on startet. |
| 7 | **MQTT-Passwort als Kommandozeilenargument** von `mosquitto_pub` (sichtbar in der Prozessliste im Container). | niedrig | Entfällt mit Python-Client. |
| 8 | **`.DS_Store`-Dateien in `rootfs/`** | niedrig | Entfernen, in `.gitignore` aufnehmen. |
| 9 | **Keine `DOCS.md`.** HA zeigt im Reiter "Dokumentation" die `DOCS.md`. | niedrig | Im neuen Add-on `DOCS.md` anlegen. |
| 10 | **Nur Lesen:** keine Befehlstopics. | Funktionslücke | Optional in Phase 6. |
| 11 | **Fehlende Messwerte:** Modulation, Abgastemperatur, Betriebsstunden. Rücklauf auf `0808` ist unsicher (siehe Abschnitt 6). | Funktionslücke | Entdeckungsmodus. |

**Ergebnis:** Das Add-on ist für den Betrieb brauchbar und nach meiner Prüfung nicht kaputt. Die Hauptprobleme sind funktional (Takten nicht messbar, veraltete Werte bei Ausfall), nicht die Home-Assistant-Konformität. Offen bleibt Punkt 1, den ich nicht abschließend klären konnte. HA benennt Add-ons in der Doku inzwischen "Apps", die Funktionsweise ist dieselbe.

## 4. Architektur

```
vito-optolink/
  config.yaml            # Optionen, uart, devices, services: mqtt:need
  Dockerfile             # ARG BUILD_FROM=...; python3 + paho-mqtt
  CHANGELOG.md  README.md  DOCS.md  icon.png  logo.png
  run.sh                 # bashio-Optionen -> Umgebung -> exec python
  app/
    p300.py              # Serieller Treiber, Framing, Prüfsumme
    datapoints.py        # YAML laden, Dekodierung/Kodierung
    poller.py            # Abfragegruppen mit Intervallen
    mqtt_bridge.py       # Discovery, Zustände, Verfügbarkeit, optional Befehle
    derived.py           # Takt-Kennzahlen
    discover.py          # Entdeckungsmodus (nur lesen)
  datapoints/
    20CB.yaml            # geprüfte Datenpunkte
    candidates_20CB.yaml # Kandidaten aus Repo/Wiki, ungeprüft
  tests/
    test_p300.py  test_decode.py  fake_serial.py
```

Der Treiber ist die einzige Komponente, die den Port öffnet. Alle Zugriffe laufen über eine Warteschlange, weil der Optolink nur eine Anfrage gleichzeitig verarbeitet.

## 5. Phasen

### Phase 0: Gerüst
- `config.yaml` nach Muster von `kaco-powador`, `version: "0.1.0"`, `image:` auskommentiert.
- Optionen: `device`, `poll_fast_s` (10), `poll_slow_s` (60), `mode` (`run` | `discover`), `allow_write` (false), `log_level`.
- Dockerfile mit Default für `BUILD_FROM`, Python und `paho-mqtt>=2.0`.
- **Abnahme:** Add-on baut in HA, startet, Log zeigt die Optionen.

### Phase 1: P300-Treiber
- Quelle der Wahrheit ist `src/framer.c` im Repo `vcontrold`: Seriell 4800 Baud, 8E2. Reset `0x04`, Aktivierung `0x16 0x00 0x00`, Telegramme beginnen mit `0x41`, Funktionen Lesen `0x01` und Schreiben `0x02`.
- Genaue Rahmenfelder, Prüfsummenberechnung und Zeitverhalten **aus dem C-Code ableiten und gegen Mitschnitte prüfen**, nicht aus dem Gedächtnis.
- Timeouts, Wiederholungen und Neu-Synchronisation nach Fehlern.
- **Tests:** Einheitentests gegen einen simulierten Regler (`fake_serial.py`), Prüfsummen- und Dekodiertests.
- **Abnahme (an der Heizung):** `00F8` liefert die Geräte-ID `20CB`; die Außentemperatur stimmt mit dem Display überein.

### Phase 2: Datenpunkte, Poller, MQTT
- YAML-Format je Datenpunkt: `id`, `name`, `addr`, `len`, `type` (`temp10`, `temp1`, `uint`, `enum`, `bool`, `hours`), `group` (`fast`/`slow`), `ha` (Sensorart, Einheit, `device_class`, `state_class`).
- Start mit den 24 bisherigen Sensoren, **gleiche `unique_id`s und gleiches Gerät**, damit Dashboards erhalten bleiben.
- Discovery wie bisher, dazu `availability_topic` und Last-Will.
- Fehlerbehandlung: Bei Fehlern keinen Wert veröffentlichen, nach mehreren Fehlern Verfügbarkeit auf offline.
- **Abnahme:** Alle bisherigen Sensoren zeigen dieselben Werte wie im alten Add-on (beide parallel nicht möglich, weil nur ein Prozess den Port öffnet, also nacheinander vergleichen).

### Phase 3: Entdeckungsmodus (`mode: discover`)
Nur Lesen, Ergebnis als Datei unter `/share/vito-optolink/` und im Log.
1. **Geräte-ID und Kandidaten:** `00F8` lesen, Kandidatenliste laden.
2. **Plausibilitätstest:** Wertebereich, Änderungsverhalten über die Zeit, Zusammenhang mit Brennerstart.
3. **Abgleich mit Display:** Du gibst Referenzwerte ein (Option oder Datei), der Assistent sucht passende Adressen. Mehrere Messzeitpunkte reduzieren Zufallstreffer.
4. **Änderungs-Scan:** Schnappschuss eines gezielten Adressbereichs, Zustandsänderung auslösen, Differenz anzeigen (z. B. Brennerstart für Modulation).
5. Ausgabe: bewertete Liste (sicher / unklar / falsch) und ein Vorschlag für `datapoints/20CB.yaml`.
- **Bereiche für den Scan** (aus den Adressen in deiner Config abgeleitet, zu prüfen): `0800`–`08FF`, `2000`–`2FFF`, `5500`–`55FF`, `6500`–`65FF`, `7500`–`77FF`.
- **Offene Fragen, nur durch Test an der Heizung zu klären:** maximale Länge pro Lesebefehl, Scan-Geschwindigkeit, Verhalten des Reglers bei vielen Anfragen.
- **Abnahme:** Klärung von `55D3` (Modulation?) und `0808` (Rücklauf oder Abgas?).

### Phase 4: Takt-Sensoren
- Schnelle Gruppe (alle 10 s): Flamme, Brennerleistung (sobald Adresse belegt), Kesseltemperatur Ist/Soll, Rücklauf, Pumpenstatus.
- Abgeleitet aus Brennerstarts und Betriebsstunden: **mittlere Laufzeit pro Start** und **Starts pro Stunde** (gleitend).
- Optional: Sensor "letzte Laufzeit" und "letzte Pause" aus Flammenwechseln.
- **Abnahme:** Die Kennzahlen stimmen über einen Tag mit manueller Zählung der Brennerstarts überein.

### Phase 5: Aufräumen und Dokumentation
- `README.md`, `DOCS.md` (Optionen, udev-Regel, Entdeckungsmodus, Takt-Sensoren), `CHANGELOG.md`.
- Hinweis zur Umstellung vom alten Add-on.

### Phase 6: Schreiben (optional, standardmäßig aus)
- Nur mit `allow_write: true` **und** Eintrag in einer Freigabeliste.
- Zuerst die risikoarmen Werte: Betriebsart, Raum- und Warmwassersollwerte. Codierebene 2 und Zeitprogramme **nicht** in der ersten Version.
- Vor jedem Schreiben Plausibilitätsgrenzen, danach Zurücklesen zur Kontrolle.
- MQTT-Befehlstopics, in HA als `number`/`select`.
- **Abnahme:** Wert setzen, am Regler-Display prüfen, ursprünglichen Wert wiederherstellen.

## 6. Bekannte Unsicherheiten (aus der Analyse)

| Thema | Stand |
|---|---|
| Modulation | Im Repo hat `getBrennerStatus` für `20CB` Adresse `55D3`, 1 Byte, Einheit `PR1`. Wahrscheinlich Brennerleistung in Prozent, **nicht belegt**. `A38F` gilt im Repo nur für ID `2053`. |
| Rücklauf `0808` | Im Repo ist `0808` für `20CB` gleichzeitig `getTempAbgas` und `getTempRL17A`. Beides kann nicht stimmen. Zu klären im Entdeckungsmodus. |
| Solar, Zirkulation | Adressen `6560`–`6568`, `CF30`, `6515`: Nur sinnvoll, wenn die Anlage das hat. Plausibilitätstest. |
| Heizkurve `27D3`/`27D4` | Im Repo für `20CB` als Neigung und Niveau geführt, vor dem Schreiben prüfen. |
| Websuche | Bestätigt `20CB` als Vitotronic-200-HO1-Familie, aber **keine** der Adressen. Hinweis auf ViessData21-Datenpunktlisten als Quelle, die ich nicht eingesehen habe. |

## 7. Tests ohne Heizung

Ich kann keine Hardware ansprechen. Deshalb:
- Einheitentests mit simuliertem Regler und aufgezeichneten Telegrammen.
- Syntaxprüfung der YAML-Dateien und der Discovery-Payloads.
- **Hardwaretests führen wir gemeinsam:** Du startest das Add-on, ich werte das Log aus. Für jede Phase gibt es eine Abnahme (siehe oben).

## 8. Migration und Rückfall

1. Altes Add-on stoppen (nur ein Prozess darf den Port öffnen).
2. Neues Add-on starten, Sensoren vergleichen.
3. Bei Problemen: neues stoppen, altes starten. Die `unique_id`s sind identisch, es entstehen keine doppelten Entitäten.
4. Wenn alles läuft: altes Add-on deinstallieren und aus dem README entfernen.

## 9. Offene Entscheidungen

1. **Schreiben** in Phase 6 gewünscht, oder erst einmal nur lesen?
2. **Abfrageintervall** für die schnelle Gruppe: 10 s als Start, bei Bedarf kürzer, wenn der Regler es verträgt.
3. **Sensor-Namen:** deutsche Namen wie im alten Add-on beibehalten?
