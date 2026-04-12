#!/usr/bin/env node
/**
 * Warema WMS interactive test CLI
 *
 * Usage:
 *   WMS_SERIAL_PORT=/dev/ttyUSB0 WMS_PAN_ID=1A2B WMS_KEY=0123...CDEF WMS_CHANNEL=17 node test-cli.js
 *
 * For network parameter discovery:
 *   WMS_SERIAL_PORT=/dev/ttyUSB0 WMS_PAN_ID=FFFF node test-cli.js
 */

import readline from 'readline';
import WmsVbStickUsb from './srv/warema-wms-venetian-blinds/index.js';

const PORT    = process.env.WMS_SERIAL_PORT || '/dev/ttyUSB0';
const CHANNEL = parseInt(process.env.WMS_CHANNEL || '17');
const PANID   = process.env.WMS_PAN_ID        || 'FFFF';
const KEY     = process.env.WMS_KEY           || '00112233445566778899AABBCCDDEEFF';

const rl = readline.createInterface({ input: process.stdin, output: process.stdout });

let stick;
let scannedDevices = [];
let ready = false;

// ─── helpers ──────────────────────────────────────────────────────────────────

function prompt() {
    if (ready) rl.question('\nwms> ', handleCommand);
}

function printHelp() {
    console.log(`
Commands:
  scan              Scan for WMS devices on the network
  list              List scanned devices
  status            Show stick status + statistics
  wave   <snr>      Send waveRequest to wake device, then try pos command
  watch  <snr> [ms] Register device and poll every ms (default 500) — operate with remote to see values
  open   <snr>      Open blind to 0%
  close  <snr>      Close blind to 100%
  stop   <snr>      Stop moving blind
  pos    <snr> <p>  Set position 0–100 (and optional tilt: pos <snr> <p> <t>)
  weather           Show last weather station data
  help              Show this help
  exit              Quit
`);
}

function handleCommand(line) {
    const parts = line.trim().split(/\s+/);
    const cmd   = parts[0];

    switch (cmd) {
        case 'scan':
            console.log('Scanning...');
            scannedDevices = [];
            stick.scanDevices({ autoAssignBlinds: false });
            break;

        case 'list':
            if (scannedDevices.length === 0) {
                console.log('No devices yet — run scan first.');
            } else {
                console.log('\n       SNR   Hex    Type  Description');
                scannedDevices.forEach(d =>
                    console.log(`  ${String(d.snr).padStart(8)}  ${d.snrHex}  ${d.type.padStart(4)}  ${d.typeStr || ''}`)
                );
            }
            prompt();
            break;

        case 'status':
            console.log(JSON.stringify(stick.getStatus(), null, 2));
            prompt();
            break;

        case 'open': {
            const snr = parts[1];
            if (!snr) { console.log('Usage: open <snr>'); prompt(); break; }
            ensureBlind(snr);
            stick.vnBlindSetPosition(snr, 0, 0);
            console.log(`→ opening ${snr}`);
            prompt();
            break;
        }

        case 'close': {
            const snr = parts[1];
            if (!snr) { console.log('Usage: close <snr>'); prompt(); break; }
            ensureBlind(snr);
            stick.vnBlindSetPosition(snr, 100, 0);
            console.log(`→ closing ${snr}`);
            prompt();
            break;
        }

        case 'stop': {
            const snr = parts[1];
            if (!snr) { console.log('Usage: stop <snr>'); prompt(); break; }
            ensureBlind(snr);
            stick.vnBlindStop(snr);
            console.log(`→ stopping ${snr}`);
            prompt();
            break;
        }

        case 'pos': {
            const snr = parts[1];
            const pos = parseInt(parts[2]);
            const tilt = parts[3] !== undefined ? parseInt(parts[3]) : 0;
            if (!snr || isNaN(pos)) { console.log('Usage: pos <snr> <0–100> [tilt]'); prompt(); break; }
            ensureBlind(snr);
            stick.vnBlindSetPosition(snr, pos, tilt);
            console.log(`→ ${snr} → pos=${pos} tilt=${tilt}`);
            prompt();
            break;
        }

        case 'wave': {
            const snr = parts[1];
            if (!snr) { console.log('Usage: wave <snr>'); prompt(); break; }
            ensureBlind(snr);
            stick.vnBlindWaveRequest(snr);
            console.log(`→ wave sent to ${snr} — wait 1s then try pos command`);
            prompt();
            break;
        }

        case 'watch': {
            const snr = parts[1];
            const interval = parseInt(parts[2] || '500');
            if (!snr) { console.log('Usage: watch <snr> [interval_ms]'); prompt(); break; }
            ensureBlind(snr);
            const pollMs = Math.max(interval, 5000); // library minimum is 5000ms
            stick.setPosUpdInterval(pollMs);
            stick.setWatchMovingBlindsInterval(200);
            console.log(`Watching ${snr} — polling every ${pollMs}ms. Operate with remote to see passive messages. Ctrl+C to stop.`);
            // don't re-prompt, updates will print as they arrive
            break;
        }

        case 'weather': {
            const s = stick.getStatus();
            if (s.weather && s.weather.ts) {
                console.log(JSON.stringify(s.weather, null, 2));
            } else {
                console.log('No weather data received yet.');
            }
            prompt();
            break;
        }

        case 'help':
        case '':
            printHelp();
            prompt();
            break;

        case 'exit':
        case 'quit':
            console.log('bye.');
            process.exit(0);

        default:
            console.log(`Unknown command: ${cmd} — type help`);
            prompt();
    }
}

function ensureBlind(snr) {
    const blinds = stick.vnBlindsList();
    const exists = blinds.some(b => String(b.snr) === String(snr) || b.name === snr);
    if (!exists) {
        stick.vnBlindAdd(parseInt(snr), snr);
        console.log(`  (auto-registered blind ${snr})`);
    }
}

// ─── WMS callback ─────────────────────────────────────────────────────────────

function wmsCallback(err, msg) {
    if (err) {
        console.error('\n[error]', err);
        prompt();
        return;
    }
    if (!msg) return;

    switch (msg.topic) {
        case 'wms-vb-init-completion':
            ready = true;
            console.log(`\nStick ready on ${PORT} (channel=${CHANNEL} panid=${PANID})`);
            if (PANID === 'FFFF') {
                console.log('Discovery mode — follow the on-screen instructions to get your network params.');
                console.log('Press Ctrl+C when done.\n');
            } else {
                printHelp();
                prompt();
            }
            break;

        case 'wms-vb-scanned-devices':
            scannedDevices = msg.payload.devices;
            console.log(`\nScan complete — ${scannedDevices.length} device(s) found:`);
            console.log('       SNR   Hex    Type  Description');
            scannedDevices.forEach(d =>
                console.log(`  ${String(d.snr).padStart(8)}  ${d.snrHex}  ${d.type.padStart(4)}  ${d.typeStr || ''}`)
            );
            prompt();
            break;

        case 'wms-vb-blind-position-update':
            console.log(`\n[pos update] snr=${msg.payload.snr} pos=${msg.payload.position} tilt=${msg.payload.angle} moving=${msg.payload.moving}`);
            prompt();
            break;

        case 'wms-vb-rcv-weather-broadcast':
            const w = msg.payload.weather;
            console.log(`\n[weather] snr=${w.snr} temp=${w.temp}°C wind=${w.wind}m/s lux=${w.lumen} rain=${w.rain}`);
            prompt();
            break;

        case 'wms-vb-network-params':
            console.log('\n*** Network parameters discovered:');
            console.log(`    Channel: ${msg.payload.channel}`);
            console.log(`    PanId:   ${msg.payload.panId}`);
            console.log(`    Key:     ${msg.payload.networkKey}`);
            break;

        default:
            console.log(`\n[${msg.topic}]`, JSON.stringify(msg.payload));
            prompt();
    }
}

// ─── start ────────────────────────────────────────────────────────────────────

console.log(`Connecting to ${PORT} (channel=${CHANNEL} panid=${PANID})...`);

stick = new WmsVbStickUsb(PORT, CHANNEL, PANID, KEY, {}, wmsCallback);

rl.on('close', () => process.exit(0));
