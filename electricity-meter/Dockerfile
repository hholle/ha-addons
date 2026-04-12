ARG BUILD_FROM
FROM $BUILD_FROM

ENV LANG C.UTF-8

RUN pip3 install --no-cache-dir pyserial "paho-mqtt>=2.0" iec62056

COPY run.sh /
COPY zaehler.py /

RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
