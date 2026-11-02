#!/usr/bin/env bash
# Phase 2, step 2: expose yente to the AgentCore agent through nginx, over HTTPS with basic auth (plan phase 2).
#   YENTE_DOMAIN=yente.example.com LE_EMAIL=you@example.com scripts/aws/11-yente-route.sh
# Run ON THE BOX. Prerequisites: a DNS record for YENTE_DOMAIN pointing at the box's Elastic IP, and port 80 open.
#
# The basic-auth password is generated here and written to secrets/yente.password (mode 600). It is not printed. Move
# it into Secrets Manager and then delete the file. Re-running keeps the existing password.
# Idempotent: re-running renews nothing unless the certificate is missing.
set -euo pipefail
cd "$(dirname "$0")/../.."

: "${YENTE_DOMAIN:?set YENTE_DOMAIN (the yente hostname)}"
: "${LE_EMAIL:?set LE_EMAIL (the Lets Encrypt contact address)}"
COMPOSE="docker compose -f docker-compose.prod.yml -f docker-compose.yente-route.yml"
LE_VOLUME="aml-prod_letsencrypt"
TEMPLATE="docker/nginx/yente/yente.conf.tpl"
CONF="docker/nginx/yente/yente.conf"
SECRETS_DIR="secrets"
HTPASSWD="$SECRETS_DIR/yente.htpasswd"
PASSFILE="$SECRETS_DIR/yente.password"

mkdir -p "$SECRETS_DIR" docker/nginx/yente
chmod 700 "$SECRETS_DIR"

# 1. Basic-auth file (created once). The password is random and stored only in the 600-mode file.
if [ ! -f "$HTPASSWD" ]; then
  PASS=$(openssl rand -base64 24 | tr -d '=+/' | cut -c1-32)
  umask 077
  printf '%s' "$PASS" > "$PASSFILE"
  docker run --rm httpd:2.4-alpine htpasswd -nbB yente "$PASS" > "$HTPASSWD"
  unset PASS
  chmod 600 "$PASSFILE" "$HTPASSWD"
  echo "wrote $PASSFILE (move the password to Secrets Manager, then delete it)"
fi

# 2. Port-80-only vhost first, so the HTTP-01 challenge works before the certificate exists.
sed -e "s/__YENTE_DOMAIN__/$YENTE_DOMAIN/g" -e '/# BEGIN TLS/,/# END TLS/d' "$TEMPLATE" > "$CONF"
$COMPOSE up -d nginx
$COMPOSE exec -T nginx nginx -t

# 3. Certificate (issued once; renewed by the same weekly job as the API certificate, see scripts/issue_cert.sh).
if ! docker run --rm -v "$LE_VOLUME:/etc/letsencrypt:ro" alpine:3.20 test -f "/etc/letsencrypt/live/$YENTE_DOMAIN/fullchain.pem"; then
  $COMPOSE --profile cert run --rm certbot certonly --webroot -w /var/www/certbot \
    -d "$YENTE_DOMAIN" --email "$LE_EMAIL" --agree-tos --no-eff-email --non-interactive
  echo "certificate issued for $YENTE_DOMAIN"
fi

# 4. Full vhost with TLS, test, reload.
sed -e "s/__YENTE_DOMAIN__/$YENTE_DOMAIN/g" -e '/# BEGIN TLS/d' -e '/# END TLS/d' "$TEMPLATE" > "$CONF"
$COMPOSE exec -T nginx nginx -t
$COMPOSE exec -T nginx nginx -s reload

cat <<EOF
yente route is up at https://$YENTE_DOMAIN (basic auth, rate limited).
Next:
  1. Move the password in $PASSFILE into Secrets Manager (for example aml/agent, key YENTE_BASIC_PASSWORD), then delete the file.
  2. On the box, set COMPOSE_FILE in .env to:  docker-compose.prod.yml:docker-compose.yente-route.yml
  3. Check:  curl -sI https://$YENTE_DOMAIN/  should answer 401 without credentials.
EOF
