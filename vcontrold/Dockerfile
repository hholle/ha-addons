# https://developers.home-assistant.io/docs/add-ons/configuration#add-on-dockerfile
ARG BUILD_FROM
FROM $BUILD_FROM

ARG TEMPIO_VERSION BUILD_ARCH
RUN \
    curl -sSLf -o /usr/bin/tempio \
    "https://github.com/home-assistant/tempio/releases/download/${TEMPIO_VERSION}/tempio_${BUILD_ARCH}"

# Build vcontrold and vclient from source (native musl binary, no libc6-compat needed)
# https://github.com/openv/vcontrold — v0.98.12 is the latest release
RUN apk add --no-cache --virtual .build-deps \
        cmake make gcc g++ libxml2-dev git linux-headers \
    && git clone --depth 1 --branch v0.98.12 https://github.com/openv/vcontrold.git /tmp/vcontrold-src \
    && cmake -B /tmp/vcontrold-src/build /tmp/vcontrold-src -DCMAKE_BUILD_TYPE=Release -DMANPAGES=OFF \
    && cmake --build /tmp/vcontrold-src/build --parallel \
    && cp /tmp/vcontrold-src/build/vcontrold /tmp/vcontrold-src/build/vclient /usr/bin/ \
    && apk del .build-deps \
    && rm -rf /tmp/vcontrold-src

RUN apk add --no-cache libxml2 mosquitto-clients

# Copy root filesystem
COPY rootfs /

RUN chmod a+x \
        /etc/services.d/vcontrold/run \
        /etc/services.d/vcontrold/finish \
        /etc/vcontrold/heizung.sh \
        /usr/bin/run-vito \
        /usr/bin/publish-discovery
