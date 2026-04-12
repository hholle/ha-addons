/**
 * Warema WMS → MQTT Bridge
 *
 * Connects to a Warema WMS USB stick, scans for devices, publishes HA MQTT
 * discovery payloads, and forwards position/tilt commands bidirectionally.
 *
 * Flow:
 *   1. WMS stick initialises (serial port open, network joined)
 *   2. Devices are discovered via scan or force_devices config
 *   3. Each device gets an HA MQTT discovery payload (retained)
 *   4. Position/tilt updates from the stick are published to MQTT
 *   5. Commands from HA (set/set_position/set_tilt) are forwarded to the stick
 *
 * Topic schema:
 *   warema/<snr>/state          → open | closed | stopped | opening | closing
 *   warema/<snr>/position       → 0–100
 *   warema/<snr>/tilt           → angle value
 *   warema/<snr>/availability   → online | offline
 *   warema/<snr>/set            ← OPEN | CLOSE | STOP
 *   warema/<snr>/set_position   ← 0–100
 *   warema/<snr>/set_tilt       ← angle value
 *   warema/bridge/state         → online | offline (LWT)
 */

import warema from './warema-wms-venetian-blinds/index.js';
import log from './logger.js';
import mqtt from 'mqtt';

// ─── Graceful shutdown ────────────────────────────────────────────────────────
// Publish offline state explicitly before exit so HA updates immediately
// rather than waiting for the MQTT LWT timeout.
process.on('SIGINT', function () {
    log.info('Shutting down...');
    if (client) {
        client.publish('warema/bridge/state', 'offline', {retain: true}, () => {
            client.end(false, {}, () => process.exit(0));
        });
    } else {
        process.exit(0);
    }
});

// ─── Configuration (injected as env vars by the s6 run script) ───────────────
const mqttServer      = process.env.MQTT_SERVER      || 'mqtt://localhost';
const ignoredDevices  = process.env.IGNORED_DEVICES  ? process.env.IGNORED_DEVICES.split(',') : [];
// force_devices bypasses radio scan: "SNR:TYPE,SNR:TYPE" e.g. "1132530:2A,1152669:25"
const forceDevices    = process.env.FORCE_DEVICES    ? process.env.FORCE_DEVICES.split(',') : [];
const pollingInterval = process.env.POLLING_INTERVAL || 30000; // ms between position polls
const movingInterval  = process.env.MOVING_INTERVAL  || 1000;  // ms between polls while moving

// device_groups maps multiple SNRs to one shared HA device.
// Format: "GroupName:snr1,snr2,snr3" — semicolon-separated for multiple groups.
// Example: "Terrassendach:1132530,1152669,1195016"
// Parsed into a map: { snr → { name, id } } where id is the shared HA device identifier.
const deviceGroupMap = {}; // { '<snr>': { name: 'Terrassendach', id: 'warema_group_Terrassendach' } }
if (process.env.DEVICE_GROUPS) {
    process.env.DEVICE_GROUPS.split(';').forEach(group => {
        const [name, snrs] = group.split(':');
        if (!name || !snrs) return;
        const groupId = 'warema_group_' + name.replace(/\s+/g, '_');
        snrs.split(',').forEach(snr => {
            deviceGroupMap[snr.trim()] = { name: name.trim(), id: groupId };
        });
    });
}

const settingsPar = {
    wmsChannel:    process.env.WMS_CHANNEL    || 17,
    wmsKey:        process.env.WMS_KEY        || '00112233445566778899AABBCCDDEEFF',
    wmsPanid:      process.env.WMS_PAN_ID     || 'FFFF', // FFFF = network discovery mode
    wmsSerialPort: process.env.WMS_SERIAL_PORT || '/dev/ttyUSB0',
};

// ─── Runtime state ────────────────────────────────────────────────────────────
// devices tracks last known position/angle, type, and availability per SNR.
// Fields: { type, position, angle, lastSeen, offline }
const devices = {};
// pendingCommands holds debounce timer IDs keyed by "<snr>:<command>" so rapid
// slider moves don't queue up — only the last value within the debounce window is sent.
const pendingCommands = {};
let stickUsb; // WMS USB stick instance (warema-wms-venetian-blinds)
let client;   // MQTT client

// ─── Helpers ──────────────────────────────────────────────────────────────────

/** Returns an MQTT publish error callback that logs failures with the topic. */
function pubErr(topic) {
    return (err) => { if (err) log.error('MQTT publish error on ' + topic + ': ' + err.message); };
}

/**
 * Snaps a tilt value to the nearest discrete step.
 * Type 2A (Lamellendach) motors only respond to a fixed set of angles; intermediate
 * values are ignored or snapped by the motor firmware.
 *
 * The WMS protocol encodes angles as: byte = clamp(percent/100 * 75, -75, 75) + 127
 * Protocol scale is -100% to +100%. Community-documented steps for venetian slat motors
 * (type 20/21) are: -100, -66, -33, 0, 33, 66, 100.
 *
 * For type 2A (Lamellendach), the motor's physical range is -60% to +100% (not -100%),
 * because the lamella rotation mechanism has a different travel limit than slat blinds.
 * -60% = closed (raw byte 0x52), +100% = open. We reuse the community step intervals
 * within this range, anchored at the motor's actual minimum (-60 instead of -100).
 */
const TILT_STEPS_2A = [-60, -33, 0, 33, 66, 100];

function snapTilt2A(value) {
    return TILT_STEPS_2A.reduce((nearest, step) =>
        Math.abs(step - value) < Math.abs(nearest - value) ? step : nearest
    );
}

/**
 * Debounces a stick command by key. If the same key fires again within `delay` ms
 * the previous pending call is cancelled and replaced. This prevents slider drags
 * in HA from queueing dozens of serial commands.
 */
function debounceCmd(key, fn, delay = 700) {
    if (pendingCommands[key]) clearTimeout(pendingCommands[key]);
    pendingCommands[key] = setTimeout(() => {
        delete pendingCommands[key];
        fn();
    }, delay);
}

// ─── Device registration ──────────────────────────────────────────────────────

/**
 * Publishes an HA MQTT discovery payload for the given WMS device and
 * registers it with the WMS stick for position polling.
 *
 * element: { snr: string|number, type: string|number }
 *   snr  — serial number (decimal string or number)
 *   type — WMS device type (hex string '25', '2A', ... or decimal number)
 *
 * Called from:
 *   - wms-vb-init-completion (force_devices mode)
 *   - wms-vb-scanned-devices (scan mode)
 *   - wms-vb-rcv-weather-broadcast (auto-register weather stations)
 *   - homeassistant/status = online (re-publish after HA restart)
 */
function registerDevice(element) {
    log.info('Registering ' + element.snr)

    // HA MQTT discovery topic for cover entities
    const topic = 'homeassistant/cover/' + element.snr + '/' + element.snr + '/config';
    const availability_topic = 'warema/' + element.snr + '/availability';

    // Shared availability: device is online AND bridge is online
    const base_payload = {
        availability: [
            {topic: 'warema/bridge/state'},
            {topic: availability_topic}
        ],
        unique_id: element.snr,
        name: null  // null = use device name as entity name
    };

    // Normalise type to integer: hex string '2A' → 42, number 6 → 6
    const typeInt = typeof element.type === 'number' ? element.type : parseInt(element.type, 16);

    /**
     * Builds the HA device object for this SNR.
     * identifiers must be an array — a bare number causes HA to create a new device
     * for every entity instead of grouping them under one device.
     *
     * If the SNR belongs to a device_group, all SNRs in that group share the same
     * identifiers → they appear as one device in HA with multiple entities.
     * Without a group, each SNR gets its own device named "<model> <snr>".
     */
    const group = deviceGroupMap[String(element.snr)];
    const makeDevice = (model) => group
        ? { identifiers: [group.id], manufacturer: 'Warema', name: group.name }
        : { identifiers: ['warema_' + String(element.snr)], manufacturer: 'Warema', model, name: model + ' ' + element.snr };

    let model;
    let payload;

    switch (typeInt) {
        case 0x06: // Weather station eco — publishes illuminance, temp, wind, rain
            model = 'Weather station eco';
            payload = {
                ...base_payload,
                device: makeDevice(model)
            };

            // Weather station has 4 separate HA sensor entities
            client.publish('homeassistant/sensor/' + element.snr + '/illuminance/config', JSON.stringify({
                ...payload,
                state_topic: 'warema/' + element.snr + '/illuminance/state',
                device_class: 'illuminance',
                unique_id: element.snr + '_illuminance',
                object_id: element.snr + '_illuminance',
                unit_of_measurement: 'lx',
            }), {retain: true});

            client.publish('homeassistant/sensor/' + element.snr + '/temperature/config', JSON.stringify({
                ...payload,
                state_topic: 'warema/' + element.snr + '/temperature/state',
                device_class: 'temperature',
                unique_id: element.snr + '_temperature',
                object_id: element.snr + '_temperature',
                unit_of_measurement: '°C',
            }), {retain: true});

            client.publish('homeassistant/sensor/' + element.snr + '/wind/config', JSON.stringify({
                ...payload,
                state_topic: 'warema/' + element.snr + '/wind/state',
                device_class: 'wind_speed',
                unique_id: element.snr + '_wind',
                object_id: element.snr + '_wind',
                unit_of_measurement: 'm/s',
            }), {retain: true});

            client.publish('homeassistant/binary_sensor/' + element.snr + '/rain/config', JSON.stringify({
                ...payload,
                state_topic: 'warema/' + element.snr + '/rain/state',
                device_class: 'moisture',
                unique_id: element.snr + '_rain',
                object_id: element.snr + '_rain',
            }), {retain: true});

            client.publish(availability_topic, 'online', {retain: true});
            devices[element.snr] = { type: typeInt };
            // Weather station broadcasts passively — no need to add to stick polling
            return;

        case 0x07: // WMS Remote — no HA entity needed
            return;

        case 0x09: // WMS WebControl Pro — no HA entity needed
            return;

        case 0x20: // Plug receiver — cover with position + tilt
            model = 'Plug receiver';
            payload = {
                ...base_payload,
                device: makeDevice(model),
                // position_open/position_closed deliberately omitted: HA defaults (100=open,
                // 0=closed) are used and we invert WMS values ourselves in the bridge.
                command_topic:      'warema/' + element.snr + '/set',
                state_topic:        'warema/' + element.snr + '/state',
                position_topic:     'warema/' + element.snr + '/position',
                set_position_topic: 'warema/' + element.snr + '/set_position',
                tilt_status_topic:  'warema/' + element.snr + '/tilt',
                tilt_command_topic: 'warema/' + element.snr + '/set_tilt',
                tilt_closed_value: -100,
                tilt_opened_value:  100,
                tilt_min: -100,
                tilt_max:  100,
            };
            break;

        case 0x21: // Actuator UP — cover with position + tilt (no state_topic)
            model = 'Actuator UP';
            payload = {
                ...base_payload,
                device: makeDevice(model),
                // position_open/position_closed deliberately omitted: see type 0x20 comment.
                command_topic:      'warema/' + element.snr + '/set',
                position_topic:     'warema/' + element.snr + '/position',
                set_position_topic: 'warema/' + element.snr + '/set_position',
                tilt_status_topic:  'warema/' + element.snr + '/tilt',
                tilt_command_topic: 'warema/' + element.snr + '/set_tilt',
                tilt_closed_value: -100,
                tilt_opened_value:  100,
                tilt_min: -100,
                tilt_max:  100,
            };
            break;

        case 0x24: // Smart socket — basic on/off switch (TODO: implement)
            model = 'Smart socket';
            payload = {
                ...base_payload,
                device: makeDevice(model),
                state_topic:   'warema/' + element.snr + '/state',
                command_topic: 'warema/' + element.snr + '/set',
            };
            break;

        case 0x25: // Vertical awning / Seitenmarkise — cover with position only
            model = 'Vertical awning';
            payload = {
                ...base_payload,
                device: makeDevice(model),
                // position_open/position_closed deliberately omitted: see type 0x20 comment.
                state_topic:        'warema/' + element.snr + '/state',
                command_topic:      'warema/' + element.snr + '/set',
                position_topic:     'warema/' + element.snr + '/position',
                set_position_topic: 'warema/' + element.snr + '/set_position',
            };
            break;

        case 0x2A:
            // Lamellendach / Motor Wenden — tilt-only cover.
            // The WMS ang parameter controls lamella rotation: -60 = closed, 100 = open.
            // The pos parameter is unused by this motor type.
            // blindGetPos is not supported → optimistic: HA assumes commands succeed.
            // Commissioning required: the WMS stick SNR must be registered as an
            // authorised sender in WMS Studio Pro before the motor will respond.
            model = 'Lamellendach';
            payload = {
                ...base_payload,
                device: makeDevice(model),
                // No set_position_topic: this motor only supports tilt (ang), not position (pos).
                // OPEN/CLOSE commands map to ang=100/-60 respectively.
                command_topic:      'warema/' + element.snr + '/set',
                tilt_command_topic: 'warema/' + element.snr + '/set_tilt',
                tilt_status_topic:  'warema/' + element.snr + '/tilt',
                tilt_min:          -60,
                tilt_max:           100,
                tilt_opened_value:  100,
                tilt_closed_value:  -60,
            };
            break;

        default:
            log.info('Unrecognized device type: ' + element.type);
            return;
    }

    if (ignoredDevices.includes(element.snr.toString())) {
        log.info('Ignoring device ' + element.snr + ' (type ' + element.type + ')');
    } else {
        log.info('Adding device ' + element.snr + ' (type ' + element.type + ')');
        const blind = stickUsb.vnBlindAdd(parseInt(element.snr), element.snr.toString());
        // Type 2A (Lamellendach) doesn't reliably respond to blindGetPos — mark noPoll so
        // the stick library skips it during periodic polls, preventing serial queue blockage.
        if (typeInt === 0x2A) blind.noPoll = true;
        // lastSeen starts at registration time; watchdog uses this as baseline.
        devices[element.snr] = { type: typeInt, lastSeen: Date.now() };
        client.publish(availability_topic, 'online', {retain: true}, pubErr(availability_topic));
        client.publish(topic, JSON.stringify(payload), {retain: true}, pubErr(topic));
    }
}

// ─── WMS stick callback ───────────────────────────────────────────────────────

/**
 * Receives all events from the WMS library.
 * Called with (err, null) on error, (null, msg) on message.
 * msg.topic identifies the event type; msg.payload carries the data.
 */
function callback(err, msg) {
    if (err) {
        log.error(err);
    }
    if (msg) {
        switch (msg.topic) {
            case 'wms-vb-init-completion':
                // Stick is ready on the radio network. Set polling intervals then
                // either register from force_devices (skip scan) or do a radio scan.
                log.info('Warema stick ready');
                stickUsb.setPosUpdInterval(pollingInterval);
                stickUsb.setWatchMovingBlindsInterval(movingInterval);

                // Stale-device watchdog: first fires after 4 poll cycles (giving devices time
                // to respond to the initial startup poll), then repeats every 1.5 cycles.
                // Devices that haven't replied in 3 poll cycles are marked offline and
                // removed from the stick's poll list to stop blocking the serial queue.
                // They are re-added automatically when they respond again.
                setTimeout(() => setInterval(() => {
                    const threshold = pollingInterval * 3;
                    Object.keys(devices).forEach(snr => {
                        const d = devices[snr];
                        // Skip devices that don't support blindGetPos (type 2A)
                        if (d.type === 0x2A) return;
                        if (d.offline) return; // already marked offline

                        if (Date.now() - d.lastSeen > threshold) {
                            log.warn('Device ' + snr + ' has not responded to polls for ' +
                                Math.round((Date.now() - d.lastSeen) / 1000) + 's — marking offline and removing from poll list');
                            d.offline = true;
                            stickUsb.vnBlindRemove(snr);
                            client.publish('warema/' + snr + '/availability', 'offline', {retain: true});

                            // Re-add the device after a quiet period so the radio channel
                            // can recover. The device firmware stops responding when flooded
                            // with retries; a 5-minute break is enough to reset it.
                            setTimeout(() => {
                                if (!devices[snr] || !devices[snr].offline) return; // already recovered
                                log.info('Device ' + snr + ' quiet period elapsed — re-adding to poll list');
                                stickUsb.vnBlindAdd(parseInt(snr), snr);
                                devices[snr].lastSeen = Date.now();
                                devices[snr].offline = false;
                                client.publish('warema/' + snr + '/availability', 'online', {retain: true});
                            }, 60 * 1000); // 1 minute
                        }
                    });
                }, pollingInterval * 1.5), pollingInterval * 4);

                if (forceDevices && forceDevices.length) {
                    // force_devices: register known devices directly without scanning.
                    // Use this for type 2A motors that require commissioning,
                    // or to speed up startup when devices are already known.
                    // Format: "SNR:TYPE,SNR:TYPE" where TYPE is the WMS hex type code.
                    log.info('force_devices set — skipping scan');
                    forceDevices.forEach(deviceString => {
                        const [snr, type] = deviceString.split(':');
                        registerDevice({snr, type: type || '25'});
                    });
                    log.info('Registered devices:\n' + JSON.stringify(stickUsb.vnBlindsList(), null, 2));
                } else {
                    log.info('Scanning for WMS devices...');
                    stickUsb.scanDevices({autoAssignBlinds: false});
                }
                break;

            case 'wms-vb-scanned-devices':
                // Radio scan complete — register each found device.
                log.info('Scanned devices:\n' + JSON.stringify(msg.payload, null, 2));
                msg.payload.devices.forEach(element => registerDevice(element));
                log.info('Registered devices:\n' + JSON.stringify(stickUsb.vnBlindsList(), null, 2));
                break;

            case 'wms-vb-rcv-weather-broadcast':
                // Weather stations broadcast passively without being polled.
                // Auto-register on first broadcast if not already known.
                log.silly('Weather broadcast:\n' + JSON.stringify(msg.payload, null, 2));
                if (!devices[msg.payload.weather.snr]) {
                    registerDevice({snr: msg.payload.weather.snr, type: 0x06});
                }
                client.publish('warema/' + msg.payload.weather.snr + '/illuminance/state', msg.payload.weather.lumen.toString(), {retain: true});
                client.publish('warema/' + msg.payload.weather.snr + '/temperature/state', msg.payload.weather.temp.toString(), {retain: true});
                client.publish('warema/' + msg.payload.weather.snr + '/wind/state',        msg.payload.weather.wind.toString(), {retain: true});
                client.publish('warema/' + msg.payload.weather.snr + '/rain/state',        msg.payload.weather.rain ? 'ON' : 'OFF', {retain: true});
                break;

            case 'wms-vb-blind-position-update':
                // Position/tilt update from polling or while blind is moving.
                // Ignored for unknown devices (type 2A doesn't respond to blindGetPos).
                log.debug('Position update:\n' + JSON.stringify(msg.payload, null, 2));
                if (!devices[msg.payload.snr]) break;

                // Device responded — stamp last seen and recover from offline if needed
                devices[msg.payload.snr].lastSeen = Date.now();
                if (devices[msg.payload.snr].offline) {
                    log.info('Device ' + msg.payload.snr + ' is back online');
                    devices[msg.payload.snr].offline = false;
                    stickUsb.vnBlindAdd(parseInt(msg.payload.snr), msg.payload.snr.toString());
                    client.publish('warema/' + msg.payload.snr + '/availability', 'online', {retain: true});
                }

                if (typeof msg.payload.position !== 'undefined') {
                    // WMS position: 0 = fully retracted (open), 100 = fully extended (closed).
                    // HA MQTT cover default: 100 = open, 0 = closed.
                    // We store the raw WMS value internally (used when sending position commands)
                    // and publish the inverted value (100 - wmsPos) so HA displays correctly.
                    const wmsPos = msg.payload.position;
                    const haPos  = 100 - wmsPos;
                    devices[msg.payload.snr].position = wmsPos;
                    client.publish('warema/' + msg.payload.snr + '/position', '' + haPos, {retain: true});

                    // Publish cover state based on position (only when movement stops)
                    if (msg.payload.moving === false) {
                        if (wmsPos === 0)
                            client.publish('warema/' + msg.payload.snr + '/state', 'open', {retain: true});
                        else if (wmsPos === 100)
                            client.publish('warema/' + msg.payload.snr + '/state', 'closed', {retain: true});
                        else
                            client.publish('warema/' + msg.payload.snr + '/state', 'stopped', {retain: true});
                    }
                }
                if (typeof msg.payload.angle !== 'undefined') {
                    devices[msg.payload.snr].angle = msg.payload.angle;
                    client.publish('warema/' + msg.payload.snr + '/tilt', '' + msg.payload.angle, {retain: true});
                }
                break;

            default:
                log.info('UNKNOWN MESSAGE: ' + JSON.stringify(msg, null, 2));
        }

        // Heartbeat: keep bridge availability topic fresh on every WMS event
        client.publish('warema/bridge/state', 'online', {retain: true});
    }
}

// ─── Initialise WMS stick ─────────────────────────────────────────────────────
stickUsb = new warema(
    settingsPar.wmsSerialPort,
    settingsPar.wmsChannel,
    settingsPar.wmsPanid,
    settingsPar.wmsKey,
    {},
    callback
);

// ─── MQTT (skipped in network discovery mode) ─────────────────────────────────
// In discovery mode (wmsPanid = FFFF) the stick logs network parameters when
// a remote is pressed. No MQTT connection is needed in that case.
if (settingsPar.wmsPanid !== 'FFFF') {

client = mqtt.connect(mqttServer, {
    username: process.env.MQTT_USER,
    password: process.env.MQTT_PASSWORD,
    will: {
        // LWT: published by the broker if the bridge disconnects unexpectedly
        topic:   'warema/bridge/state',
        payload: 'offline',
        retain:  true
    }
});

client.on('connect', function () {
    log.info('Connected to MQTT: ' + mqttServer);
    client.subscribe([
        'warema/+/set',           // OPEN / CLOSE / STOP
        'warema/+/set_position',  // 0–100
        'warema/+/set_tilt',      // angle value
        'homeassistant/status'    // detect HA restarts → re-publish discovery
    ]);
});

client.on('error', function (error) {
    log.error('MQTT error: ' + error.toString());
});

client.on('message', function (topic, message) {
    const [scope, device, command] = topic.split('/');
    message = message.toString();

    log.debug('scope=' + scope + ' device=' + device + ' command=' + command + ' message=' + message);

    // Re-publish all discovery payloads when HA comes back online after a restart
    if (scope === 'homeassistant' && command === 'status') {
        if (message === 'online') {
            log.info('Home Assistant online — re-publishing discovery payloads');
            Object.keys(devices).forEach(snr => {
                const el = stickUsb.vnBlindsList().find(b => String(b.snr) === String(snr));
                if (el) registerDevice(el);
            });
        }
        return;
    }

    // Route warema/<snr>/<command> to the WMS stick
    switch (command) {
        case 'set':
            switch (message) {
                case 'CLOSE':
                    log.debug('Closing ' + device);
                    if (devices[device]?.type === 0x2A) {
                        // Lamellendach: ang controls rotation; pos is ignored. -60 = closed.
                        stickUsb.vnBlindSetPosition(device, 0, -60);
                        devices[device].angle = -60;
                        client.publish('warema/' + device + '/tilt', '-60', {retain: true});
                    } else {
                        stickUsb.vnBlindSetPosition(device, 100, parseInt(devices[device]?.angle) || 0);
                    }
                    client.publish('warema/' + device + '/state', 'closing');
                    break;
                case 'OPEN':
                    log.debug('Opening ' + device);
                    if (devices[device]?.type === 0x2A) {
                        // Lamellendach: ang controls rotation; pos is ignored. 100 = open.
                        stickUsb.vnBlindSetPosition(device, 0, 100);
                        devices[device].angle = 100;
                        client.publish('warema/' + device + '/tilt', '100', {retain: true});
                    } else {
                        stickUsb.vnBlindSetPosition(device, 0, parseInt(devices[device]?.angle) || 0);
                    }
                    client.publish('warema/' + device + '/state', 'opening');
                    break;
                case 'STOP':
                    log.debug('Stopping ' + device);
                    stickUsb.vnBlindStop(device);
                    break;
            }
            break;

        case 'set_position':
            // Debounced: slider drags fire many messages; only send the last one.
            // HA sends position in HA scale (100=open, 0=closed); invert to WMS scale (0=open, 100=closed).
            if (!devices[device]) { log.warn('set_position for unknown device ' + device); break; }
            debounceCmd(device + ':pos', () => {
                const wmsPos = 100 - parseInt(message);
                log.debug('Setting ' + device + ' to HA ' + message + '% (WMS ' + wmsPos + '), angle ' + devices[device].angle);
                stickUsb.vnBlindSetPosition(device, wmsPos, parseInt(devices[device].angle) || 0);
            });
            break;

        case 'set_tilt':
            // Debounced: slider drags fire many messages; only send the last one.
            // For type 2A (Lamellendach): ang controls lamella rotation; pos is ignored.
            if (!devices[device]) { log.warn('set_tilt for unknown device ' + device); break; }
            debounceCmd(device + ':tilt', () => {
                let ang = parseInt(message);
                // Type 2A motors only respond to discrete tilt steps — snap to nearest.
                if (devices[device].type === 0x2A) ang = snapTilt2A(ang);
                log.debug('Setting ' + device + ' tilt to ' + ang + '° (requested: ' + message + '°), position ' + devices[device].position);
                stickUsb.vnBlindSetPosition(device, parseInt(devices[device].position) || 0, ang);
                // Optimistic state for type 2A: motor doesn't respond to blindGetPos polls,
                // so publish the snapped tilt value immediately rather than waiting for a poll update.
                if (devices[device].type === 0x2A) {
                    devices[device].angle = ang;
                    client.publish('warema/' + device + '/tilt', '' + ang, {retain: true});
                }
            });
            break;

        default:
            log.info('Unrecognised command from HA: ' + command);
    }
});

} // end: if not discovery mode
