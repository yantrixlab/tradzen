#!/usr/bin/env bash
# Creates the .env for a new server with a random secret key and a random admin password.
#   ./deploy/prepare.sh signals.example.com [admin-user]
# It never overwrites an existing .env. The admin password is printed once: write it down (it can be changed in .env later).
set -euo pipefail
cd "$(dirname "$0")/.."

DOMAIN="${1:-}"
ADMIN="${2:-admin}"
if [ -z "$DOMAIN" ]; then
  echo "usage: $0 <domain> [admin-user]    e.g. $0 signals.example.com" >&2
  exit 1
fi
if [ -e .env ]; then
  echo ".env already exists - not touching it." >&2
  exit 1
fi

rand() { head -c 48 /dev/urandom | base64 | tr -d '/+=\n' | cut -c1-"$1"; }
SECRET="$(rand 64)"
PASSWORD="$(rand 20)"

sed -e "s|^QXS_DOMAIN=.*|QXS_DOMAIN=${DOMAIN}|" \
    -e "s|^QXS_ADMIN_USER=.*|QXS_ADMIN_USER=${ADMIN}|" \
    -e "s|^QXS_ADMIN_PASSWORD=.*|QXS_ADMIN_PASSWORD=${PASSWORD}|" \
    -e "s|^QXS_SECRET_KEY=.*|QXS_SECRET_KEY=${SECRET}|" \
    .env.example > .env
chmod 600 .env

echo "Created .env for https://${DOMAIN}"
echo "  admin user:     ${ADMIN}"
echo "  admin password: ${PASSWORD}"
echo "Next:  docker compose up -d --build"
