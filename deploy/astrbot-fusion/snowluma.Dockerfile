# Build with the official v1.10.1 Linux x64 lite release as release.tar.gz.
# Parent retains the existing QQ client and startup/volume layout.
ARG SNOWLUMA_BASE_IMAGE=motricseven7/snowluma:v1.10.0@sha256:5680ab02064204668330685548829f12586ad4b5e51d3cc8274461d2c854b749
FROM ${SNOWLUMA_BASE_IMAGE}
COPY release.tar.gz /tmp/snowluma-release.tar.gz
RUN echo 'b01c081e1e05411fa192ff58623251569e519d4b5c3ad67c4582538fb9c68f2b  /tmp/snowluma-release.tar.gz' | sha256sum -c - \
    && rm -rf /app/snowluma \
    && mkdir /app/snowluma \
    && tar -xzf /tmp/snowluma-release.tar.gz -C /app/snowluma \
    && chmod -R a+rX /app/snowluma \
    && rm /tmp/snowluma-release.tar.gz
