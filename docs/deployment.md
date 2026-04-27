# Deployment

The app is single-origin: FastAPI serves the UI (`GET /` → `frontend-next/dist/index.html`, hashed bundles at `/assets/*`) and the API. One process, one domain. **TLS is mandatory** — passwords, bearer tokens, and user API keys all move over the wire; without HTTPS they leak.

The UI is a Vite/React app under `frontend-next/`. The Dockerfile builds it in a Node 20 stage (`npm ci && npm run build`) and copies the resulting `dist/` into the Python runtime image — no Node ships in production. When `frontend-next/dist/` is missing (e.g. local `python3 run.py` runs without a build), the backend transparently falls back to the legacy vanilla-JS frontend at `frontend/`.

Two documented paths: **Fly.io** (recommended, minimal ops overhead, TLS + volumes built-in) and **self-hosted VPS with Caddy** (more DIY, more control).

## Deploying to Fly.io

Repo ships with `Dockerfile`, `fly.toml`, `.dockerignore`, and `.python-version` tuned for this deploy. Config paths (`DB_PATH`, `RUNTIME_DB_PATH`, `EXPORTS_DIR`) are env-driven and point at `/data/...` on the Fly volume in `fly.toml`.

**Cost estimate:** `shared-cpu-1x@2gb` + 10GB volume ≈ $12-13/mo.

### Pre-flight

- [ ] Install flyctl: `curl -L https://fly.io/install.sh | sh` (or `brew install flyctl` on macOS).
- [ ] `fly auth login` — browser-interactive.
- [ ] `fly auth whoami` confirms you're in.

### One-time setup

```bash
# Pick a unique app name (globally unique on Fly); edit fly.toml's `app = ...`
# if the default collides. Primary region is already set to `iad` in fly.toml.
fly apps create <your-app-name>

# 10GB volume: ~2.3GB for the NFL DBs, plenty of room for the runtime DB and
# CSV exports to grow.
fly volumes create nfl_data --region iad --size 10 --yes

# Secrets — env vars that aren't in fly.toml for security. Generate the
# encryption key with the Fernet one-liner below if you don't already have one.
fly secrets set \
    SETTINGS_ENCRYPTION_KEY='<your Fernet key>' \
    REGISTRATION_INVITE_CODE='<a secret code>'

# Deploy — builds the Docker image, pushes to Fly's registry, starts a machine
# with the volume attached. Healthcheck on /health must pass for the deploy
# to succeed.
fly deploy
```

Generate a Fernet key locally if needed:

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### Seed the databases

The ~1.3GB nflverse DB isn't in the Docker image (it'd bloat every deploy); it lives on the volume. The Dockerfile's CMD creates `/data/runtime`, `/data/nflverse`, and `/data/exports` on every startup, so a fresh volume is ready for uploads without any prep. Push the DB via SFTP — for an in-place re-seed against a running app, upload to a `.new` filename first, then atomically rename so the running DuckDB handle isn't corrupted mid-write:

```bash
echo "put NFLVERSE/data/nflverse.duckdb /data/nflverse/nflverse.db.new" | \
  fly ssh sftp shell
fly ssh console -C "mv /data/nflverse/nflverse.db.new /data/nflverse/nflverse.db"
```

The upload goes through Fly's ssh proxy at your home upload speed — 1.3GB takes 5-10 minutes on a typical home pipe. play_by_play lives inside this single DuckDB file along with the other 29 tables; no separate `pbp.db` anymore.

After the upload finishes and the rename is done, restart the machine so the app reopens DuckDB handles against the freshly-seeded file:

```bash
fly machine list     # grab the machine ID
fly machine restart <machine-id>
```

### Verify

```bash
curl https://<your-app-name>.fly.dev/health
# {"status":"ok"}

curl https://<your-app-name>.fly.dev/auth/status
# {"has_users":false,"authenticated":false,"user":null,"invite_required":true}

# Schema + row-count sanity against the seeded DuckDB file.
fly ssh console -C "python3 -c 'import duckdb; c=duckdb.connect(\"/data/nflverse/nflverse.db\",read_only=True); print(c.execute(\"SELECT COUNT(*) FROM players\").fetchone(), c.execute(\"SELECT COUNT(*) FROM play_by_play\").fetchone())'"
# Should print counts matching your local copy.
```

Visit `https://<your-app-name>.fly.dev/` in a browser, register with your invite code, paste an API key into Settings, ask a question. If all that works you're live.

### Ongoing ops

- **Logs:** `fly logs` (tail) or `fly logs --since 1h`.
- **Shell:** `fly ssh console`.
- **Deploy code changes:** `git push` and `fly deploy`. Volume and secrets persist across deploys; only the app container is replaced.
- **Backups:** `fly ssh console -C "sh -c 'sqlite3 /data/runtime/runtime.sqlite3 \".backup /data/runtime/backup-$(date +%F).sqlite3\"'"` — or pull a copy locally with `fly ssh sftp get /data/runtime/runtime.sqlite3 ./runtime-backup.sqlite3` periodically. The runtime DB holds users, conversations, and encrypted API keys; the nflverse DBs are reproducible.
- **Rotate the encryption key or invite code:** `fly secrets set KEY=new_value` → Fly restarts the machine automatically. **Do not rotate `SETTINGS_ENCRYPTION_KEY` without a migration plan** — every stored user API key becomes undecryptable the moment the old key is gone.
- **Scale memory:** `fly scale memory 4096` if pbp queries start hitting OOM.

### Custom domain (optional)

`fly certs create yourhost.com` then add the DNS records Fly prints. TLS is auto-provisioned in seconds.

## Alternative: self-hosted VPS (Caddy + systemd)

More control, more ops work. TLS still mandatory — don't skip it.

### Pre-flight

- [ ] `SETTINGS_ENCRYPTION_KEY` set in the server's env. Losing the key makes every stored API key unreadable.
- [ ] App bound to `127.0.0.1`, not `0.0.0.0`. Verify with `ss -tlnp | grep 8001`.
- [ ] Caddy (or nginx) terminates TLS. `curl -I https://yourhost.com` returns 200 with a valid cert.
- [ ] Firewall blocks inbound 8001 from the internet. Only 80/443 open.
- [ ] Backup job for `data/runtime.sqlite3` verified (restore into a scratch DB to confirm).

### Setup

```bash
git clone <repo> /srv/nflverse && cd /srv/nflverse
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Place nflverse.duckdb under NFLVERSE/data/ — see the build scripts in NFLVERSE-DB

python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

`/srv/nflverse/.env`:

```
SETTINGS_ENCRYPTION_KEY=<output from the command above>
HOST=127.0.0.1                            # loopback — reachable only via reverse proxy
FORWARDED_ALLOW_IPS=127.0.0.1             # trust X-Forwarded-For only from local proxy
ALLOWED_ORIGINS=https://yourhost.com      # optional
AUTH_TOKEN_TTL_DAYS=30                    # optional; default 30
REGISTRATION_INVITE_CODE=<a secret>       # optional; open signup if unset
```

### Run

```bash
uvicorn backend.server.app:app \
    --host 127.0.0.1 --port 8001 \
    --proxy-headers --forwarded-allow-ips 127.0.0.1
```

- No `--reload`, no `--workers N > 1` (rate limiter is in-memory per-process).

### Caddy

`/etc/caddy/Caddyfile`:

```
yourhost.com {
    encode zstd gzip
    reverse_proxy 127.0.0.1:8001
}
```

`systemctl reload caddy` and Caddy provisions the cert on first HTTPS request, sets `X-Forwarded-*` headers, and adds HSTS automatically.

### systemd unit

`/etc/systemd/system/nflverse.service`:

```ini
[Unit]
Description=nflverse API + UI
After=network.target

[Service]
User=nflverse
WorkingDirectory=/srv/nflverse
EnvironmentFile=/srv/nflverse/.env
ExecStart=/srv/nflverse/.venv/bin/uvicorn backend.server.app:app \
    --host 127.0.0.1 --port 8001 \
    --proxy-headers --forwarded-allow-ips 127.0.0.1
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload && systemctl enable --now nflverse
journalctl -u nflverse -f   # tail logs
```

### Backups

`data/runtime.sqlite3` holds everything mutable. Nightly `sqlite3 data/runtime.sqlite3 '.backup /backups/runtime-$(date +%F).sqlite3'` in cron is the whole story. Keep `.env` backups separate from DB backups — an attacker with both can decrypt stored keys.

## Dev path

`python3 run.py` → open `http://localhost:8001/`. Defaults bind to `127.0.0.1`; set `HOST=0.0.0.0 python3 run.py` to expose to LAN.
