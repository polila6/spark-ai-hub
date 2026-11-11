# Isolated page browser (Chromium via Playwright) for pages that need JavaScript.
# - own container: no volumes, no secrets, read-only root, no capabilities, non-root, memory/pids limits
# - its only networks are `webctl` (the backend asks for a page) and `webout` (internal; the filtering proxy is the only
#   way out, see ../webproxy) — it cannot reach the Spark, the tailnet or any private address
# - a NEW browser process and an empty profile per page; nothing survives a request; downloads, popups, dialogs,
#   permissions and service workers are refused
FROM mcr.microsoft.com/playwright:v1.64.1-noble
ENV NODE_ENV=production PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
WORKDIR /srv
COPY package.json /srv/package.json
RUN npm install --omit=dev --no-audit --no-fund && npm cache clean --force
COPY server.js /srv/server.js
USER pwuser
EXPOSE 3000
CMD ["node", "/srv/server.js"]
