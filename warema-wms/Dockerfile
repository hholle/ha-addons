# https://developers.home-assistant.io/docs/apps/configuration/
# Base image default lives here: build.yaml is deprecated.
ARG BUILD_FROM=ghcr.io/home-assistant/base-python:latest
FROM $BUILD_FROM

ENV LANG C.UTF-8

RUN pip3 install --no-cache-dir pyserial "paho-mqtt>=2.0"

COPY run.sh /
COPY app /app

RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
