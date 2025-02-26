#!/bin/bash

/usr/bin/vcontrold -s 

RESULT=`/usr/bin/vclient -h 127.0.0.1:3002 -f /etc/vcontrold/heizung.cmd -t /etc/vcontrold/heizung.tpl 2>&1`
CODE=${PIPESTATUS[0]}

echo $RESULT
echo $code

#exit

hasError=0
while IFS='|' read -r item value status; do
#echo "$item" 
#echo "$value"
   if [ -z "$value" ] || [ -z "$status" ] || [ "$status" != "OK" ]; then
	hasError=1
    else
        /usr/bin/mosquitto_pub -u $MQTT_USER -P $MQTT_PASSWORD -i vcontrol -h $MQTT_HOST -r -t "vcontrold/$item" -m "$value"
#	echo $item
#        echo $value
	#curl -s -X PUT -H "Content-Type: text/plain" -d $value "http://sh:8080/rest/items/"$item"/state"
    fi
done <<< "$RESULT"

kill `pidof vcontrold`

if [ "$hasError" -eq 1 ]; then
  printf "STATUS: %s\nRESULT: %s" "$CODE" "$RESULT" > "/var/log/Heizungsdatenfehler_$(date +"%d.%m.%Y_%H:%M:%S")"
fi
