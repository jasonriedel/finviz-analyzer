#!/usr/bin/env bash
# setup.sh — one-time setup for Finviz Analyzer
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "=== Finviz Analyzer Setup ==="

# ── Python virtual environment ─────────────────────────────────────────────
if [ ! -d ".venv" ]; then
  echo "[1/5] Creating Python virtual environment (python3.12)..."
  python3.12 -m venv .venv
else
  echo "[1/5] Virtual environment already exists."
fi

source .venv/bin/activate

echo "[2/5] Installing Python dependencies..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

echo "[3/5] Installing Playwright browsers..."
playwright install chromium
playwright install-deps chromium 2>/dev/null || true

# ── Docker / Postgres ──────────────────────────────────────────────────────
echo "[4/5] Starting Postgres via Docker Compose..."
if ! command -v docker &>/dev/null; then
  echo "ERROR: Docker is not installed. Please install Docker Desktop first."
  exit 1
fi

docker compose up -d

echo "Waiting for Postgres to be ready..."
for i in {1..20}; do
  if docker exec finviz_postgres pg_isready -U finviz -d finviz_analyzer &>/dev/null; then
    echo "Postgres is ready."
    break
  fi
  sleep 2
done

# ── Logs dir ───────────────────────────────────────────────────────────────
mkdir -p logs

# ── Cron job ───────────────────────────────────────────────────────────────
echo "[5/5] Setting up cron job (7:00 AM Arizona MST = 14:00 UTC)..."

CRON_CMD="0 14 * * * $DIR/run.sh >> $DIR/logs/cron.log 2>&1"
# Add only if not already present
( crontab -l 2>/dev/null | grep -qF "$DIR/run.sh" ) && {
  echo "Cron job already exists, skipping."
} || {
  ( crontab -l 2>/dev/null; echo "$CRON_CMD" ) | crontab -
  echo "Cron job added: $CRON_CMD"
}

echo ""
echo "=== Setup complete ==="
echo ""
echo "To run manually:"
echo "  source .venv/bin/activate && python main.py"
echo ""
echo "To check cron:"
echo "  crontab -l"
echo ""
echo "To view logs:"
echo "  tail -f logs/finviz.log"
