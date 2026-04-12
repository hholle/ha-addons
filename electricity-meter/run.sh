#!/usr/bin/with-contenv bashio

export MQTT_HOST=$(bashio::services mqtt "host")
export MQTT_USER=$(bashio::services mqtt "username")
export MQTT_PASSWORD=$(bashio::services mqtt "password")
export SERIAL_DEVICE=$(bashio::config 'device')

bashio::log.info "Stromzähler starting — device=${SERIAL_DEVICE} mqtt=${MQTT_HOST}"

exec python3 -u /zaehler.py
