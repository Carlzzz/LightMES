#!/bin/sh
set -e

if [ -z "$MQTT_USER" ] || [ -z "$MQTT_PASSWORD" ]; then
  echo "MQTT_USER and MQTT_PASSWORD are required"
  exit 1
fi

mosquitto_passwd -b -c /mosquitto/config/passwd "$MQTT_USER" "$MQTT_PASSWORD"
exec mosquitto -c /mosquitto/config/mosquitto.conf
