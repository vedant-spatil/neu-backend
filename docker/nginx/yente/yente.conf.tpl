# yente route for the AgentCore agent (plan phase 2). Rendered by scripts/aws/11-yente-route.sh into yente.conf (not committed).
# Placeholder __YENTE_DOMAIN__ is replaced. The TLS block between the markers is left out until the certificate exists.

# Rate limit per client address for the yente route (http context, so it is valid in conf.d).
limit_req_zone $binary_remote_addr zone=yente:10m rate=10r/s;

server {
    listen 80;
    server_name __YENTE_DOMAIN__;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

# BEGIN TLS
server {
    listen 443 ssl;
    server_name __YENTE_DOMAIN__;

    ssl_certificate     /etc/letsencrypt/live/__YENTE_DOMAIN__/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/__YENTE_DOMAIN__/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    # Only the AgentCore agent uses this route. Basic auth; the password is in Secrets Manager.
    auth_basic "yente";
    auth_basic_user_file /etc/nginx/yente.htpasswd;

    location / {
        limit_req zone=yente burst=20 nodelay;
        proxy_pass http://yente:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
}
# END TLS
