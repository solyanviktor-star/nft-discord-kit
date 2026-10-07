# Hosting

What the kit needs from a host:
- **A long-running process.** The bot keeps a gateway connection to Discord and a 30-second timer.
  Serverless functions (Vercel, Netlify, Lambda, Cloudflare Workers) cannot host it.
- **HTTPS on a domain** for the verification site (wallets warn on plain http, and Discord OAuth
  redirects must match exactly). `PUBLIC_URL=https://verify.example.com`.
- **A persistent disk** for `data/`: `kit.db` (SQLite: wallets, raffles, entries, tickets),
  `session_secret`, `state.json`, ticket transcripts. Losing it loses the linked wallets and raffles.
- Python 3.11+ (the Docker image uses 3.12). About 150 MB of RAM.

Health check: `GET /healthz` answers `{"ok": true, "discord": true|false}`.

## A VPS with Docker (recommended)

On a small Linux server (any provider) with Docker and a DNS `A` record for the domain:

```bash
git clone <your repo> && cd <your repo>/discord-kit     # the copied app with config.toml
cp .env.example .env && nano .env                       # DISCORD_TOKEN, PUBLIC_URL, RPC_*
DOMAIN=verify.example.com docker compose --profile https up -d --build
docker compose logs -f kit                              # Ctrl+C stops following, not the bot
```

`docker-compose.yml` runs the app (non-root, data in the `kit-data` volume, `config.toml` mounted
read-only) and, with the `https` profile, Caddy, which gets and renews the certificate by itself
(ports 80 and 443 must be open). Without the profile, publish port 8080 and put your own proxy in front.

Setup commands run inside the container, e.g. `docker compose exec kit python -m kit.setup panels`.
Backup: `docker compose cp kit:/data/kit.db ./kit.db.bak`. Update: replace the app files, then
`docker compose up -d --build`.

## A VPS without Docker (systemd + Caddy)

```bash
sudo useradd --system --create-home kit
sudo mkdir -p /opt/nft-discord-kit && sudo chown kit /opt/nft-discord-kit
# copy the app into /opt/nft-discord-kit (config.toml and .env included), then as the kit user:
cd /opt/nft-discord-kit && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
sudo cp deploy/kit.service /etc/systemd/system/nft-discord-kit.service
sudo systemctl daemon-reload && sudo systemctl enable --now nft-discord-kit
journalctl -u nft-discord-kit -f
```

Set `[web] host = "localhost"` so only Caddy reaches the app. Install Caddy (caddyserver.com),
put the domain into `deploy/Caddyfile` (or set `DOMAIN`), copy it to `/etc/caddy/Caddyfile` and
`sudo systemctl reload caddy`. Back up `/opt/nft-discord-kit/data/kit.db`.

## Railway

1. New project from the GitHub repo (root directory: the copied app). Railway builds the Dockerfile.
2. Variables: `DISCORD_TOKEN`, `PUBLIC_URL`, `RPC_*`, optional `DISCORD_CLIENT_SECRET`,
   `SESSION_SECRET`. Railway sets `PORT`; the app follows it.
3. Add a **volume** mounted at `/data` (the image sets `KIT_DATA_DIR=/data`).
4. Commit `config.toml` to the private repo (it holds no secrets), or bake it in.
5. Networking: generate a domain or add your own; use it as `PUBLIC_URL`.

## Render

A **Web Service** from the repo with the Dockerfile, plan with an always-on instance (free instances
sleep, which disconnects the bot). Add a **persistent disk** mounted at `/data`. Environment as for
Railway; Render sets `PORT`. Health check path `/healthz`. Custom domain under Settings.

## Fly.io

```bash
fly launch --no-deploy            # uses the Dockerfile; pick a region
fly volumes create kit_data --size 1
fly secrets set DISCORD_TOKEN=... PUBLIC_URL=https://<app>.fly.dev RPC_ETHEREUM=...
```

In `fly.toml`: mount the volume (`[mounts] source = "kit_data"`, `destination = "/data"`), set
`internal_port = 8080`, and keep one machine always running (`min_machines_running = 1`,
`auto_stop_machines = "off"`). **Do not scale beyond one machine**: SQLite on a volume belongs to one
machine, and two bots would both answer every button.

## Anything else

Any host that runs a long-lived container (or `python -m kit run`), gives HTTPS on a domain, keeps a
disk for `/data` across restarts and deploys, and restarts the process if it dies. Run exactly one
instance.

## Local test

`PUBLIC_URL=http://localhost:8080` works for trying the site on your own machine; wallets will sign
on localhost. Discord buttons then open that localhost link, so only you can use them.
