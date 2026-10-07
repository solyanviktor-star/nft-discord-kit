FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    KIT_DATA_DIR=/data

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY kit ./kit
COPY templates ./templates

# Run as a normal user; SQLite and the generated session secret live in the /data volume.
RUN useradd --create-home --uid 10001 kit && mkdir -p /data && chown kit:kit /data
USER kit
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz', timeout=4)" || exit 1

# config.toml is mounted at /app/config.toml (see docker-compose.yml); secrets come from the environment.
CMD ["python", "-m", "kit", "run"]
