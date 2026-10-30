# https://developers.home-assistant.io/docs/apps/configuration/
# Base image default lives here: build.yaml is deprecated.
ARG BUILD_FROM=ghcr.io/home-assistant/base-python:latest
FROM $BUILD_FROM

ENV LANG C.UTF-8

RUN apk add --no-cache tzdata \
 && pip3 install --no-cache-dir pyserial pyyaml "paho-mqtt>=2.0"

COPY run.sh /
COPY app /app
COPY datapoints /datapoints

RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
