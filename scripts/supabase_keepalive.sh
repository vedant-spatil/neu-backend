#!/bin/bash
# Keep-alive for a Supabase Free project. Free projects pause after a week without database activity, so this runs a
# real query through the API container's own database connection every second day.
#
# Install on the EC2 box (crontab -e), with the deployed backend folder as BACKEND_DIR if it differs:
#   0 6 */2 * * cd /home/ubuntu/neu-backend && ./scripts/supabase_keepalive.sh >> /home/ubuntu/keepalive.log 2>&1
#
# The script does not read or print .env values: the container already has DATABASE_URL.
set -euo pipefail

cd "${BACKEND_DIR:-$HOME/neu-backend}"
docker compose -f docker-compose.prod.yml exec -T api python -c "
from sqlalchemy import text
from app.db.session import engine
with engine.connect() as conn:
    conn.execute(text('SELECT 1'))
"
echo "$(date -u +%FT%TZ) keepalive ok"
