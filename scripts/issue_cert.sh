#!/usr/bin/env bash
# Let's Encrypt certificate for the API domain (plan.md section 23).
#
# Before the first run: the domain (API_DOMAIN) must point at the instance's Elastic IP, and port 80
# must be open. Put API_DOMAIN and LE_EMAIL in .env.
#
#   scripts/issue_cert.sh           first time: issues the certificate and starts nginx
#   scripts/issue_cert.sh --renew   renews it; run weekly from cron
set -euo pipefail
cd "$(dirname "$0")/.."

COMPOSE="docker compose -f docker-compose.prod.yml"
# Docker volume name = compose project name (set in docker-compose.prod.yml) + "_" + volume name.
LE_VOLUME="aml-prod_letsencrypt"

DOMAIN=$(grep -E '^API_DOMAIN=' .env | cut -d= -f2-)
EMAIL=$(grep -E '^LE_EMAIL=' .env | cut -d= -f2-)
[ -n "$DOMAIN" ] || { echo "API_DOMAIN is not set in .env" >&2; exit 1; }
[ -n "$EMAIL" ] || { echo "LE_EMAIL is not set in .env" >&2; exit 1; }

if [ "${1:-}" = "--renew" ]; then
  $COMPOSE --profile cert run --rm certbot renew --webroot -w /var/www/certbot --quiet
  $COMPOSE exec nginx nginx -s reload
  exit 0
fi

# 1. Databases, search, sanctions and the API. nginx waits for the API to be healthy.
$COMPOSE up -d postgres elasticsearch yente neo4j api

# 2. nginx refuses to start without a certificate file, so create a short-lived placeholder first.
docker run --rm -v "$LE_VOLUME:/etc/letsencrypt" alpine:3.20 sh -c "
  apk add --no-cache openssl >/dev/null &&
  mkdir -p /etc/letsencrypt/live/$DOMAIN &&
  openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
    -keyout /etc/letsencrypt/live/$DOMAIN/privkey.pem \
    -out /etc/letsencrypt/live/$DOMAIN/fullchain.pem -subj /CN=localhost"
$COMPOSE up -d nginx

# 3. Replace the placeholder with the real certificate (HTTP-01 challenge over port 80).
docker run --rm -v "$LE_VOLUME:/etc/letsencrypt" alpine:3.20 sh -c \
  "rm -rf /etc/letsencrypt/live/$DOMAIN /etc/letsencrypt/archive/$DOMAIN /etc/letsencrypt/renewal/$DOMAIN.conf"
$COMPOSE --profile cert run --rm certbot certonly --webroot -w /var/www/certbot \
  -d "$DOMAIN" --email "$EMAIL" --agree-tos --no-eff-email --non-interactive
$COMPOSE exec nginx nginx -s reload

echo "HTTPS ready: https://$DOMAIN/api/health"
echo "Add a weekly renewal to cron:  0 3 * * 1  cd $(pwd) && scripts/issue_cert.sh --renew"
