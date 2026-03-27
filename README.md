# ScreenerX.ai

**Multi-market swing trading screener** — India (Nifty) and US major indices.

ScreenerX.ai scans index universes with multi-factor technical analysis: signals, trade levels, and an explainable score. Includes a FastAPI web dashboard and a Rich CLI.

![Python 3.12+](https://img.shields.io/badge/Python-3.12+-blue?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.109+-009688?logo=fastapi&logoColor=white)

**Repository:** [github.com/udayn612/screenerX.ai](https://github.com/udayn612/screenerX.ai)

---

## Features

- **Multi-factor signals** — EMA alignment, RSI recovery, MACD crossover, support bounce, volume surge
- **Scoring** — Weighted 0–100 score with breakdown
- **Levels** — Entry, stop-loss (ATR + support), targets and risk:reward
- **Dashboard** — Filtering, sorting, sparklines, price refresh
- **Markets** — Nifty 50/100/200/500, Dow 30, Nasdaq 100, S&P 500
- **Caching** — SQLite OHLCV + scan result cache (TTL in `config.py`)
- **CLI + web** — `swing` command or `--web` for the UI

## Layout

```
src/swing/
├── analysis/     # indicators, signals, scorer, levels
├── data/         # fetchers, cache, nifty_indices, us_stocks
├── web/          # FastAPI app + static dashboard
├── config.py
└── main.py       # CLI entry
```

## Quick start

**Prerequisites:** Python 3.12+, [uv](https://docs.astral.sh/uv/) (or pip)

```bash
cd screenerx-ai
uv sync
```

**Web UI**

```bash
uv run python -m swing.main --web
# http://localhost:8000
```

**CLI**

```bash
uv run swing
uv run swing -n 50
```

## Configuration

See `src/swing/config.py` (EMA periods, signal thresholds, `CACHE_TTL_SECONDS`, rate limits, etc.).

### Google sign-in & admin (optional)

If **`GOOGLE_CLIENT_ID`** and **`GOOGLE_CLIENT_SECRET`** are set (environment variables), users must sign in with Google to use the dashboard. Data is stored in **`data/auth_users.db`**.

Also set:

- **`PUBLIC_BASE_URL`** — public site URL with no trailing slash (e.g. `https://stockscreenerx.com`). Required so OAuth redirect matches [Google Cloud Console](https://console.cloud.google.com/apis/credentials) (**Authorized redirect URI**: `{PUBLIC_BASE_URL}/auth/google/callback`).
- **`SESSION_SECRET`** — long random string for signing session cookies.
- **`SESSION_COOKIE_SECURE`** — set to `true` when serving only over HTTPS.
- **`ADMIN_EMAILS`** — comma-separated Google account emails allowed to open **`/admin`** and see who has logged in.

Omit the Google env vars to run locally without login (default).

**Docker on the VPS:** copy [`.env.example`](.env.example) to `.env`, fill in values, then `docker compose up -d --build`. Compose injects those variables automatically (see [`docker-compose.yml`](docker-compose.yml)).

## VPS (Docker + HTTPS)

On the server: install [Docker Engine](https://docs.docker.com/engine/install/) and Compose plugin, clone the repo, point DNS `A`/`AAAA` at the VPS, then:

```bash
docker compose up -d --build
```

The app listens on **127.0.0.1:8000** only. Put **[Caddy](https://caddyserver.com/)** or nginx on ports 80/443 and reverse-proxy to that address. For **screenerx.ai**, use `deploy/Caddyfile.screenerx.ai`; for a generic template see `deploy/Caddyfile.example`. Health check path for monitors: `/api/health`.

Optional: persist the SQLite cache with a volume mapping `./data:/app/data` on the `web` service if you add it to `docker-compose.yml`.

### Hostinger

Use a **[Hostinger KVM VPS](https://www.hostinger.com/vps-hosting)** — not shared web hosting. This app needs a long-running Python process and outbound HTTP; a VPS template with **Docker** (or install Docker over SSH) is appropriate.

#### Domain **screenerx.ai** (DNS)

1. **Register or connect the domain** in [hPanel](https://hpanel.hostinger.com/) (**Domains**). If `screenerx.ai` is at another registrar, either transfer it to Hostinger or keep it there and edit DNS at that registrar (same records below).
2. **Point traffic to the VPS** — open **DNS / DNS Zone** for `screenerx.ai` and set:
   - **`A`** — **Host** `@` (or blank / root) — **Points to** your VPS **IPv4** (from **VPS → Overview**).
   - **`A`** — **Host** `www` — **same VPS IPv4**.
   - Optional: **`AAAA`** for `@` and `www` if Hostinger gave you an IPv6 for the VPS and you want dual-stack.
3. Wait for DNS propagation (often minutes; can be up to 24–48 hours globally).

TLS: install [Caddy](https://caddyserver.com/) on the VPS and use **`deploy/Caddyfile.screenerx.ai`** (copy to `/etc/caddy/Caddyfile`, then `sudo systemctl reload caddy`). Caddy will request a Let’s Encrypt certificate once `@` resolves to this server and ports **80** / **443** are open. The live site will be **https://screenerx.ai** (with `www` redirecting to the apex).

**VPS setup (after DNS is planned or done)**

1. **Order & OS** — Pick a plan with enough RAM for scans (e.g. **2 GB+**). Choose **Ubuntu 24.04** or another template where you can run Docker.
2. **Firewall** — In the VPS **firewall** section (if available) or with `ufw`, allow **22** (SSH), **80**, and **443**.
3. **Deploy over SSH** (simplest for this repo):
   - Use hPanel **SSH access** (or **Root password** / key) and connect to the server.
   - Install Docker + Compose if needed ([Docker Engine install](https://docs.docker.com/engine/install/ubuntu/)).
   - Clone this repository on the VPS, then from the project directory run `docker compose up -d --build`.
   - Install **Caddy** on the host, copy `deploy/Caddyfile.screenerx.ai` to `/etc/caddy/Caddyfile`, then `sudo systemctl reload caddy`.  
     **Or** use **[Nginx Proxy Manager](https://www.hostinger.com/support/how-to-set-up-nginx-proxy-manager-using-hostinger-docker-manager/)** and add a proxy host for `screenerx.ai` / `www` → `http://127.0.0.1:8000` with SSL — see [managing Docker projects](https://www.hostinger.com/support/hostinger-vps-how-to-manage-your-docker-projects/).
4. **Docker Manager only** — You can deploy **from a Git URL** if the project is pushed to GitHub/GitLab; the root `docker-compose.yml` is picked up automatically. You still need **Caddy** (host) or **NPM** for TLS on **screenerx.ai**; [Docker Manager overview](https://www.hostinger.com/support/12040789-hostinger-docker-manager-for-vps-simplify-your-container-deployments).

Health check path: `/api/health`.

**CI/CD (GitHub → Hostinger):** Push this repo to GitHub on branch `main`, then add **`HOSTINGER_API_KEY`** (secret) and **`HOSTINGER_VM_ID`** (variable) under *Settings → Secrets and variables → Actions*. The workflow [`.github/workflows/deploy-hostinger.yml`](.github/workflows/deploy-hostinger.yml) runs [Deploy on Hostinger VPS](https://github.com/marketplace/actions/deploy-on-hostinger-vps) so each push redeploys via Docker Manager. Use **Run workflow** in the Actions tab for a manual deploy.

## Disclaimer

This content is for learning purposes only and should not be considered financial advice. Always do your own research before trading.
