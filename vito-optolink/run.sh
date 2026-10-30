#!/usr/bin/with-contenv bashio

export MQTT_HOST=$(bashio::services mqtt "host")
export MQTT_USER=$(bashio::services mqtt "username")
export MQTT_PASSWORD=$(bashio::services mqtt "password")
export SERIAL_DEVICE=$(bashio::config 'device')
export POLL_FAST_S=$(bashio::config 'poll_fast_s')
export POLL_SLOW_S=$(bashio::config 'poll_slow_s')
export MODE=$(bashio::config 'mode')
export SCAN_RANGES=$(bashio::config 'scan_ranges')
export SCAN_ROUNDS=$(bashio::config 'scan_rounds')
export SCAN_INTERVAL_S=$(bashio::config 'scan_interval_s')
export REFERENCE_TEMPS=$(bashio::config 'reference_temps')
export TAKT_WARN_STARTS_H=$(bashio::config 'takt_warn_starts_h')
export PUBLISH_CODINGS=$(bashio::config 'publish_codings')
export TZ=$(bashio::supervisor.timezone 2>/dev/null || echo UTC)
export LOG_LEVEL=$(bashio::config 'log_level')

bashio::log.info "Vito Optolink starting — device=${SERIAL_DEVICE} mqtt=${MQTT_HOST}"

cd / && exec python3 -u -m app.main
