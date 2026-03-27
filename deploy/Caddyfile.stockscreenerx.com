# HTTPS for stockscreenerx.com on your Hostinger VPS (Caddy on the host).
#
# 1. Verify domain email in hPanel (remove "Pending verification").
# 2. DNS A: @ → your VPS IPv4   |   A: www → same IP
# 3. On VPS: docker compose up -d --build (app on 127.0.0.1:8000)
# 4. sudo apt install -y caddy   # if not installed
# 5. sudo cp deploy/Caddyfile.stockscreenerx.com /etc/caddy/Caddyfile
# 6. sudo systemctl reload caddy
#
# Health: https://stockscreenerx.com/api/health

www.stockscreenerx.com {
	redir https://stockscreenerx.com{uri}
}

stockscreenerx.com {
	encode gzip
	reverse_proxy 127.0.0.1:8000
}
