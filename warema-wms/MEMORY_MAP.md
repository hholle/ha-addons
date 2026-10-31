# WMS radio motor: memory map (MB8 blocks)

Companion to `PROTOCOL.md` §5. A receiver's state and configuration are memory blocks read with `8010` and written with `8020`:

```
read   {R06<snr>8010 <block 1 byte><address 2 bytes LE><size 1 byte>}   → {r<snr>8011 <block><address LE><size><data>}
write  {R06<snr>8020 <block><address LE><size><data bytes>}             → {r<snr>8021 …}  (failure: 80FE)
```

Source: the parameter lists of the WMS studio pro 4.37.1.0 plugins (`RadioMotor`, `RadioMotorL60L70`, `Product.VerticalAwning` for radio motors). **[studio]** The lists were
decrypted locally from the installer for analysis. Addresses are the first byte of each parameter. Parameter data types and units are referenced by ids that were not decoded;
sizes are therefore **not** listed and must be taken from reading neighbouring addresses or from the observed responses. Nothing in this file was confirmed on the air
except block 1 address 0 (5 bytes), which the add-on uses for position polling **[log]**.

**Safety note:** the lists show that automations (wind, ice, rain, sun) run **inside the receiver**; the receiver gets sensor values straight from the sensors by radio. The add-on is not a
protection against wind. Writing to these blocks changes safety-relevant behaviour; this add-on does not write them.

## Device level (identical for radio motor `0x25` and slat-roof motor `0x2A`)

| Block | Address | Name | Notes |
|---|---|---|---|
| 0 | 0 | `operationMode/operatingMode` | `0` normal, `1` runtime learning, `2` EUI learning, `3` **RSSI ping-pong**, `4` check mode, `5` double power-up |
| 1 | 0 | `movementState/settings` (4 bytes) | **block 1 address 0, 5 bytes, is the position poll**: position, angle, valance 1, valance 2, moving flag |
| 1 | 4 | `movementState/isMoving` | |
| 1 | 5, 6 | `isValance1Moving`, `isValance2Moving` | |
| 1 | 8, 12 | `timeStampISEWindSensor1`, `…2` | time stamps of the wind sensors in a package |
| 1 | 16, 17 | `windMeasurementReadingISEWindSensor1`, `…2` | wind readings from package wind sensors |
| 4 | 0…399 | `drivingCauses/drivingCause0…19` | **20 entries of 20 bytes**: the last drive commands (cause, action, time, scene) |
| 6 | 0 | `motorDefaults/dataWriteState` | |
| 6 | 1 | `safetyDrive` | `0` deactivated, `1` only for safety functions, `2` for all movements |
| 6 | 2 | `specialFunctions` | |
| 6 | 3 | `installationState` | final positions pre-programmed / finished |
| 6 | 4 | `cycleCounter` | |
| 6 | 8 | `motorSerialNumber` | |
| 6 | 12 | `productType` | |
| 6 | 13 | `shutdownType` | |
| 6 | 14, 15 | `torque`, `speed` | |
| 6 | 16…19 | `motorParameter0…3` | |
| 6 | 20 | `motorRevision` | |
| 9 | 0 | `control/backupRAM` | |
| 9 | 2 | `control/productType` | |
| 9 | 3 | `control/reset` | |
| 9 | 4 | `control/factoryDefaults` | |
| 9 | 19 | **`control/noSleepCounter`** | 2 bytes, seconds, little endian (the studio writes it for "Ping and set no-sleep time") |
| 10 | 0, 32, 64, 96 | `measurementReadings/reading0…3` | last readings of up to 4 sensors, **32 bytes each**, field order below |
| 10 | 128, 132, 136, 140 | `reading0…3/time` | time stamp of each reading (4 bytes) |
| 15 | 0 | `sensorTimeStamps/secondsAlive` | seconds since power-up |
| 15 | 4, 8, 12, 16, 20, 24 | `lastReceptionSensor0…5` | **time of the last reception from sensors 0–5** (4 bytes each) |
| 31 | 16 | `rfStack/panId` | 2 bytes |
| 31 | 20 | `rfStack/radioChannel` | 1 byte |
| 37 | 0 | `productParameters/supportedProductTypes` | 12 bytes |
| 37 | 12…17 | `productType`, `controlType`, `isWithBlinds`, `isWithRuntime`, `testBenchId`, `learningMode` | |
| 81 | 24 | `deviceParameter/swVersion` | |
| 81 | 36 | `stackVersion` | |
| 81 | 43 | `deviceType` | `0x25` radio motor, `0x2A` radio motor L60/L70 |
| 81 | 44 | `commonVersion` | |

### Sensor reading layout (block 10, 32 bytes per reading)

Byte offset within a reading, in this order: `0 wind`, `1–4 photo1…photo4`, `5 dusk`, `6 batteryState`, `7 precipitation`, `8 tempOutdoor`, `9 tempIndoor`, `10 safetyContact`, `11 globalRadiation`,
`12 relativeHumidity`, `13 windDirection`, `14 airPressure`, `15 state`, `16 co2`, `17 volume`, `18 acceleration`, `19 level`, `20 frequencyLow`, `21 frequencyHigh`, `22 voltageDC`,
`23 voltageAC`, `24 powerLow`, `25 powerHigh`, `26 energy`, `27 pressure`, `28 reserve0`, `29 errorCode`, `30 setTempIndoor`, `31 reserve1`.

So the wind value of sensor 0 is block 10 address 0 and its time stamp block 10 address 128; when the receiver last heard sensor 0 is block 15 address 4.

## Product level: vertical awning on a radio motor (product type 10, device type 0x25)

All in **block 38** unless noted (address = first byte).

| Group | Parameters (address) |
|---|---|
| common | `isAbsent` 1, `isComfortAutoEnabled` 2 |
| sunControl | `isEnabled` 4, `isAvailableInTransmitter` 5, `isActiveIfAbsent` 6, `thresholdSun` 7, `thresholdCloud` 8, `switchOnDelaySun` 9, `switchOnDelayCloud` 10, `settingSun` 11 (position/angle), `settingCloud` 15, `sensorValue` 19 |
| duskControl | `isEnabled` 21, `isAvailableInTransmitter` 22, `isActiveIfAbsent` 23, `releaseTimeHour/Min` 24/25 (×2), `threshold` 32, `settingMorning` 34, `settingEvening` 38, `sensorValue` 42 |
| precipitationMonitoring | `isEnabled` 44, `isAvailableInTransmitter` 45, `isActiveIfAbsent` 46, `threshold` 47, `offDelay` 48, `setting` 49, `manualOverride` 53, `sensorValue` 54 |
| **windMonitoring** | **`isEnabled` 56**, `isAvailableInTransmitter` 57, `isActiveIfAbsent` 58, **`threshold` 59**, **`onDelay` 60**, **`offDelay` 61**, `setting` 62 (position/angle to drive to), `sensorValue` 66 |
| iceMonitoring | `isEnabled` 68, `isActiveIfAbsent` 69, `statePowerUp` 70, `threshold` 71, `endManual` 72, `endAuto` 73, `setting` 74, `sensorValue` 78 |
| temperatureControl | `isEnabled` 80, `isActiveIfAbsent` 81, `thresholdWarm` 82, `thresholdCold` 83, `switchOnDelayWarm` 84, `switchOnDelayCold` 85, `settingWarm` 86, `settingCold` 90, `sensorValue` 94 |
| centralCommand | `isEnabled` 96, `sensorValue` 97 |
| lockingContact | `isLocking` 99, `sensorValue` 100 |
| timeSwitch | `isEnabled` 102, `isActiveIfAbsent` 103, `monday` 104 … `sunday` 272 (28 bytes per day; the help says 4 timer points per weekday with time, position and angle) |
| manualOperation | `settingDown` 301, `dwellTimeManualScene` 305 |
| scene | `scene0…31` at 307, 311, 315, … (4 bytes apart, position/angle), `comfort` 435, `absent` 439, `central` 443 |
| menuSettings | visibility flags 448–461 (what the studio shows) |
| productSettings | `motorRotation` 475 |
| **sensorAllocation** | `photocellSensor1…4` 479–482, **`windSensor` 483**, `duskSensor` 484, `outsideTempSensor` 485, `insideTempSensor` 486, `precipitationSensor` 487, `lockingContact` 488 (which of the assigned sensors feeds which function) |
| **batteryStatus** | `batteryStatusSensor1…6` 490–495 (battery state of each assigned sensor) |

Other blocks of this product:

| Block | Address | Name |
|---|---|---|
| 35 | 0 | `sensorAssignment/sensorEui` (8 entries, the **serial numbers of the assigned sensors**) |
| 35 | 16 | `sensorAssignment/sensorSupport` (4 entries) |
| 36 | 0, 4, 8, 12, 16 | `iseAssignment` `actuator`, `valance1`, `valance2`, `windSensor1`, `windSensor2` (package member serial numbers) |
| 43 | 0…3 | `temperatureControl/sensValueLastWin`, `lastSetTemperature`, `lockSensValue` |

Other product plugins (slat roof L60/L70, awnings, blinds, plug receivers, actuators) have their own block-38 layouts; their lists are in the same studio plugin resources.

## What the map makes possible (read-only, nothing implemented yet)

- **Is the wind guard on?** read block 38 address 56 (`windMonitoring/isEnabled`), threshold (59) and delays (60, 61).
- **Does the receiver still hear its wind sensor?** read block 15 address 4+4·n (`lastReceptionSensor n`) and compare with `secondsAlive` (block 15 address 0); read block 10 address 0 (wind) of the sensor and
  block 15/38 battery state.
- **Which sensor is assigned?** block 35 (EUI list) and block 38 address 483 (`sensorAllocation/windSensor`).
- **What did the motor do last?** block 4 (20 drive causes, e.g. wind alarm, battery low).
- **Is the device asleep or dead?** a successful read (`8010`) or ping (`5040`) says no.
