# Tradzen: the admin panel is built in a Node stage, the backend runs on slim Python. One process, one data volume.
# Build:  docker compose build        Run:  docker compose up -d        (see DEPLOY.md)

FROM node:20-alpine AS admin
WORKDIR /src
COPY admin/package.json admin/package-lock.json ./admin/
RUN cd admin && npm ci
COPY admin ./admin
# vite writes the build to ../backend/static/admin (see admin/vite.config.ts)
RUN cd admin && npm run build

FROM python:3.13-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    QXS_ENV=production \
    QXS_DB=/app/backend/data/signals.db
WORKDIR /app/backend
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY --from=admin /src/backend/static/admin ./static/admin
COPY backend/static/landing ./static/landing
# a user without privileges owns the data folder (a named volume keeps what it finds there)
RUN useradd --system --uid 10001 --home-dir /app qxs && mkdir -p data && chown -R qxs:qxs /app
USER qxs
VOLUME ["/app/backend/data"]
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/healthz', timeout=4)" || exit 1
# ONE worker: the live market state is held in memory. --proxy-headers: the client address comes from Caddy (the port is not published, only Caddy reaches it).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8787", "--proxy-headers", "--forwarded-allow-ips=*", "--no-server-header"]
