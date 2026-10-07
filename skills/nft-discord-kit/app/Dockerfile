FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    KIT_DATA_DIR=/data

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The app, the template and config.toml when present (.dockerignore keeps .env and data/ out).
# docker-compose.yml mounts config.toml over it, so edits there need no rebuild.
COPY . .

# Run as a normal user; SQLite and the generated session secret live in the /data volume.
RUN useradd --create-home --uid 10001 kit && mkdir -p /data && chown kit:kit /data
USER kit
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://localhost:%s/healthz' % os.environ.get('PORT', '8080'), timeout=4)" || exit 1

CMD ["python", "-m", "kit", "run"]
