ARG BUILD_FROM
FROM $BUILD_FROM

ENV LANG C.UTF-8

RUN pip3 install --no-cache-dir pyserial "paho-mqtt>=2.0"

COPY run.sh /
COPY wechselrichter.py /

RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
