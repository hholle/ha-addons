# https://developers.home-assistant.io/docs/add-ons/configuration#add-on-dockerfile
ARG BUILD_FROM
FROM $BUILD_FROM

# Execute during the build of the image
ARG TEMPIO_VERSION BUILD_ARCH
RUN \
    curl -sSLf -o /usr/bin/tempio \
    "https://github.com/home-assistant/tempio/releases/download/${TEMPIO_VERSION}/tempio_${BUILD_ARCH}"

# Copy root filesystem
COPY rootfs /


RUN apk add dpkg libxml2 mosquitto-clients
#RUN dpkg -i --force-architecture vcontrold_0.98.12-16_amd64.deb
RUN chmod a+x /etc/vcontrold/heizung.sh
RUN chmod a+x /usr/bin/vcontrold
RUN chmod a+x /usr/bin/vclient
#EXPOSE 3002





