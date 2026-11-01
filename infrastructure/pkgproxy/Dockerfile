# Package proxy for workspaces: the code sandbox has no network at all; pip/npm reach this proxy through a Unix socket
# mounted into the sandbox, and the proxy only allows the package registries listed in filter.
FROM alpine:3.20.3
RUN apk add --no-cache tinyproxy socat
COPY tinyproxy.conf /etc/tinyproxy/tinyproxy.conf
COPY filter /etc/tinyproxy/filter
COPY entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh && adduser -D -u 1000 proxy
USER 1000:1000
ENTRYPOINT ["/entrypoint.sh"]
