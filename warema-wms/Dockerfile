ARG BUILD_FROM
FROM node:24-alpine AS builder

RUN apk add --no-cache python3 make g++ linux-headers

WORKDIR /app

COPY package-lock.json .
COPY package.json .

# Rebuild from source to avoid prebuilt binary issues on Alpine/musl
# https://github.com/serialport/node-serialport/issues/2438
RUN npm ci --omit=dev && npm rebuild --build-from-source

# hadolint ignore=DL3006
FROM $BUILD_FROM

RUN apk add --no-cache nodejs

WORKDIR /srv

COPY --from=builder /app/node_modules /srv/node_modules

COPY ./ /

RUN chmod +x /etc/services.d/warema-wms/run /etc/services.d/warema-wms/finish
