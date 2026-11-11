# Filtering egress proxy for the page browser: the browser container has NO route to the internet except through this
# proxy, and the proxy only connects to public addresses (every name is resolved here and the connection is pinned to
# the checked IP), so a page can never reach the Spark, the tailnet, the Docker networks or the university LAN.
FROM python:3.12.16-slim-bookworm
COPY proxy.py /srv/proxy.py
USER 1000:1000
ENTRYPOINT ["python", "-u", "/srv/proxy.py"]
