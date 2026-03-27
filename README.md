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

## VPS (Docker + HTTPS)

On the server: install [Docker Engine](https://docs.docker.com/engine/install/) and Compose plugin, clone the repo, point DNS `A`/`AAAA` at the VPS, then:

```bash
docker compose up -d --build
```

The app listens on **127.0.0.1:8000** only. Put **[Caddy](https://caddyserver.com/)** or nginx on ports 80/443 and reverse-proxy to that address. Example Caddy site block: `deploy/Caddyfile.example` (replace the hostname, reload Caddy). Health check path for monitors: `/api/health`.

Optional: persist the SQLite cache with a volume mapping `./data:/app/data` on the `web` service if you add it to `docker-compose.yml`.

### Hostinger

Use a **[Hostinger KVM VPS](https://www.hostinger.com/vps-hosting)** — not shared web hosting. This app needs a long-running Python process and outbound HTTP; a VPS template with **Docker** (or install Docker over SSH) is appropriate.

1. **Order & OS** — Pick a plan with enough RAM for scans (e.g. **2 GB+**). Choose **Ubuntu 24.04** or another template where you can run Docker.
2. **DNS** — In [Hostinger hPanel](https://hpanel.hostinger.com/), open **Domains → DNS** (or manage DNS where the domain lives). Add an **`A`** record for your hostname (e.g. `screener`) pointing to the **VPS IPv4** address shown in hPanel.
3. **Firewall** — In the VPS **firewall** section (if available) or with `ufw`, allow **22** (SSH), **80**, and **443**.
4. **Deploy over SSH** (simplest for this repo):
   - Use hPanel **SSH access** (or **Root password** / key) and connect to the server.
   - Install Docker + Compose if needed ([Docker Engine install](https://docs.docker.com/engine/install/ubuntu/)).
   - Clone this repository on the VPS, then from the project directory run `docker compose up -d --build`.
   - Put **HTTPS** in front: either install **Caddy** on the host and use `deploy/Caddyfile.example`, or use **[Nginx Proxy Manager](https://www.hostinger.com/support/how-to-set-up-nginx-proxy-manager-using-hostinger-docker-manager/)** from Hostinger’s Docker Manager and reverse-proxy to `http://127.0.0.1:8000` — see also [managing Docker projects](https://www.hostinger.com/support/hostinger-vps-how-to-manage-your-docker-projects/).
5. **Docker Manager only** — You can deploy **from a Git URL** if the project is pushed to GitHub/GitLab; the root `docker-compose.yml` is picked up automatically. Because the stack binds the app to **127.0.0.1:8000**, you still need a reverse proxy (Caddy on the host or NPM) for a public domain and TLS; [Docker Manager overview](https://www.hostinger.com/support/12040789-hostinger-docker-manager-for-vps-simplify-your-container-deployments).

Health check path: `/api/health`.

**CI/CD (GitHub → Hostinger):** Push this repo to GitHub on branch `main`, then add **`HOSTINGER_API_KEY`** (secret) and **`HOSTINGER_VM_ID`** (variable) under *Settings → Secrets and variables → Actions*. The workflow [`.github/workflows/deploy-hostinger.yml`](.github/workflows/deploy-hostinger.yml) runs [Deploy on Hostinger VPS](https://github.com/marketplace/actions/deploy-on-hostinger-vps) so each push redeploys via Docker Manager. Use **Run workflow** in the Actions tab for a manual deploy.

## Disclaimer

This content is for learning purposes only and should not be considered financial advice. Always do your own research before trading.
