#!/usr/bin/with-contenv bashio

export MQTT_HOST=$(bashio::services mqtt "host")
export MQTT_PORT=$(bashio::services mqtt "port")
export MQTT_SSL=$(bashio::services mqtt "ssl")
export MQTT_USER=$(bashio::services mqtt "username")
export MQTT_PASSWORD=$(bashio::services mqtt "password")

# Options → environment (unset optional options stay unset)
for key in wms_serial_port wms_key wms_pan_id wms_channel ignored_devices force_devices \
           device_groups polling_interval moving_interval rescan_interval log_level; do
    if bashio::config.has_value "${key}"; then
        export "${key^^}=$(bashio::config "${key}")"
    fi
done

bashio::log.info "Warema WMS starting — device=${WMS_SERIAL_PORT:-/dev/ttyUSB0} mqtt=${MQTT_HOST}"

cd / && exec python3 -u -m app.main
