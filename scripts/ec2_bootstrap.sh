#!/usr/bin/env bash
# One-time host setup for the EC2 instance (Ubuntu). Run once, as a user with sudo (plan.md section 23).
#   - 2 GB swap, a safety net for the 8 GiB t3a.large (not for normal use)
#   - vm.max_map_count for Elasticsearch
#   - Docker and the compose plugin
#   - firewall: SSH, HTTP and HTTPS only
set -euo pipefail

if [ "$(id -u)" -eq 0 ]; then
  echo "run as a normal user with sudo, not as root" >&2
  exit 1
fi

# Swap
if ! swapon --show | grep -q /swapfile; then
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

# Elasticsearch needs this kernel setting (its bootstrap check fails without it)
echo 'vm.max_map_count=262144' | sudo tee /etc/sysctl.d/99-elasticsearch.conf >/dev/null
sudo sysctl --system >/dev/null

# Docker Engine and the compose plugin (from Docker's official install script)
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sudo sh
fi
sudo usermod -aG docker "$USER"

# Firewall: SSH, HTTP (for Let's Encrypt and the redirect), HTTPS
sudo apt-get install -y ufw >/dev/null
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable

echo "Done. Log out and back in so the docker group applies."
echo "Next: copy .env.example to .env, fill it in, then run scripts/issue_cert.sh."
