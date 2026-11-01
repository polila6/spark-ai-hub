#!/bin/sh
# tinyproxy on 127.0.0.1:8888, exposed to sandboxes only as a Unix socket in /sock (no shared network).
set -e
tinyproxy -d -c /etc/tinyproxy/tinyproxy.conf &
rm -f /sock/proxy.sock
exec socat UNIX-LISTEN:/sock/proxy.sock,fork,mode=666 TCP:127.0.0.1:8888
