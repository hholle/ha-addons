#!/usr/bin/with-contenv bashio
# with-contenv is used so bashio:: functions are available when this script
# is called from run-vito (outside of s6 directly).

MQTT_HOST=$(bashio::services mqtt "host")
MQTT_USER=$(bashio::services mqtt "username")
MQTT_PASSWORD=$(bashio::services mqtt "password")

/usr/bin/vcontrold -s

# Wait for vcontrold to be ready before querying
until nc -z 127.0.0.1 3002; do sleep 0.2; done

RESULT=$(/usr/bin/vclient -h 127.0.0.1:3002 -f /etc/vcontrold/heizung.cmd -t /etc/vcontrold/heizung.tpl 2>&1)
CODE=${PIPESTATUS[0]}

clean_value() {
  local v
  v=$(echo "$1" | tr -d ',' | sed 's/%$//')
  # Only reformat if it looks like a number; pass strings (an/aus/etc.) through unchanged
  echo "$v" | awk '{
    if ($1 ~ /^-?[0-9]+(\.[0-9]+)?$/) {
      n = $1 + 0
      if (n == int(n)) printf "%d", n
      else printf "%g", n
    } else {
      print $1
    }
  }'
}

hasError=0
while IFS='|' read -r item value status; do
  if [ -z "$value" ] || [ -z "$status" ] || [ "$status" != "OK" ]; then
    hasError=1
  else
    cleaned=$(clean_value "$value")
    /usr/bin/mosquitto_pub -u "$MQTT_USER" -P "$MQTT_PASSWORD" -i vcontrol -h "$MQTT_HOST" -r -t "vcontrold/$item" -m "$cleaned"
  fi
done <<<"$RESULT"

pkill vcontrold || true

if [ "$hasError" -eq 1 ]; then
  bashio::log.warning "vclient returned errors (exit ${CODE}): ${RESULT}"
else
  bashio::log.info "Heating data published successfully"
fi
