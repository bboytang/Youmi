# Build with the official v2.1.0 Linux x64 full release as release.tar.gz.
# Parent retains the existing QQ client and startup/volume layout.
ARG SNOWLUMA_BASE_IMAGE=motricseven7/snowluma:v1.10.0@sha256:5680ab02064204668330685548829f12586ad4b5e51d3cc8274461d2c854b749
FROM ${SNOWLUMA_BASE_IMAGE}
COPY release.tar.gz /tmp/snowluma-release.tar.gz
RUN echo '7d9a29a9f52b30987e0dcdf46993757dd4eafe993e175c8ca40435a56e0b8fbc  /tmp/snowluma-release.tar.gz' | sha256sum -c - \
    && rm -rf /app/snowluma \
    && mkdir /app/snowluma \
    && tar -xzf /tmp/snowluma-release.tar.gz -C /app/snowluma \
    && chmod -R a+rX /app/snowluma \
    && cp /app/snowluma/node /usr/local/bin/node \
    && rm /tmp/snowluma-release.tar.gz
