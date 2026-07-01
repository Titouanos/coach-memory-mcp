#!/usr/bin/env bash
#
# setup-droplet.sh — prepare a fresh Ubuntu droplet to host coach-memory-mcp.
#
# Idempotent: safe to re-run. Installs Docker + compose plugin, Nginx + certbot,
# and configures ufw (deny incoming; allow SSH; allow 443 ONLY from Anthropic's
# IP ranges). Provided for reference — review before running as root.
#
#   sudo bash setup-droplet.sh
#
set -euo pipefail

DOMAIN="coach-memory.rcmlearning.com"

# -----------------------------------------------------------------------------
# Anthropic IP ranges allowed to reach 443 (custom connectors call FROM the
# Anthropic cloud). FILL THIS from the official "Anthropic IP addresses" doc and
# keep it up to date (ideally via a cron that re-applies the rules).
#   https://docs.anthropic.com  → search "IP addresses"
# Example format: ANTHROPIC_CIDRS=( "160.79.104.0/23" "1.2.3.4/32" )
# -----------------------------------------------------------------------------
ANTHROPIC_CIDRS=(
  # "<PASTE CURRENT ANTHROPIC CIDR>"
  # "<PASTE CURRENT ANTHROPIC CIDR>"
)

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "Please run as root (sudo bash setup-droplet.sh)." >&2
    exit 1
  fi
}

install_docker() {
  if command -v docker >/dev/null 2>&1; then
    echo "[docker] already installed: $(docker --version)"
  else
    echo "[docker] installing via get.docker.com convenience script ..."
    curl -fsSL https://get.docker.com | sh
  fi
  # The convenience script includes the compose plugin; verify.
  if docker compose version >/dev/null 2>&1; then
    echo "[compose] plugin present: $(docker compose version | head -1)"
  else
    echo "[compose] installing docker-compose-plugin ..."
    apt-get update -y
    apt-get install -y docker-compose-plugin
  fi
  systemctl enable --now docker
}

install_nginx_certbot() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y nginx certbot python3-certbot-nginx
  systemctl enable --now nginx
  echo "[nginx/certbot] installed."
}

configure_firewall() {
  apt-get install -y ufw
  ufw --force default deny incoming
  ufw --force default allow outgoing
  ufw allow OpenSSH

  if [[ ${#ANTHROPIC_CIDRS[@]} -eq 0 ]]; then
    echo "[ufw] WARNING: ANTHROPIC_CIDRS is empty — NOT opening 443."
    echo "[ufw] Fill ANTHROPIC_CIDRS from the official doc and re-run this script."
    echo "[ufw] (443 stays denied by default; the connector would be unreachable until you do.)"
  else
    for cidr in "${ANTHROPIC_CIDRS[@]}"; do
      echo "[ufw] allow 443/tcp from ${cidr}"
      ufw allow from "${cidr}" to any port 443 proto tcp
    done
  fi

  ufw --force enable
  ufw status verbose
}

require_root
install_docker
install_nginx_certbot
configure_firewall

cat <<EOF

============================================================================
Next steps (run manually — not automated here):

1) Point DNS: ${DOMAIN}  ->  this droplet's public IP.

2) Obtain a TLS certificate (Nginx plugin writes+reloads the vhost):
     sudo certbot --nginx -d ${DOMAIN}
   (Auto-renewal is installed as a systemd timer: 'systemctl list-timers | grep certbot')

3) Put the reverse-proxy vhost from the README in place (proxy_buffering off,
   HTTP/1.1, empty Connection header) so /mcp reaches 127.0.0.1:8000.

4) Deploy the app:
     cd /opt/coach-memory-mcp   # wherever you cloned it
     cp .env.example .env && \$EDITOR .env   # strong POSTGRES_PASSWORD + MCP_AUTH_TOKEN
     docker compose up -d --build

5) Verify:
     curl -s http://127.0.0.1:8000/healthz          # {"status":"ok","db":true}
     curl -sI https://${DOMAIN}/healthz             # via Nginx/TLS

6) Add the connector in claude.ai:
     Customize -> Connectors -> + -> Add custom connector
     URL: https://${DOMAIN}/mcp   +   Bearer token = MCP_AUTH_TOKEN
============================================================================
EOF
