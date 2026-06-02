#!/bin/bash
# One-time setup — double-click this first.
# Creates a virtualenv, installs dependencies, sets up the database, and
# creates an admin login. Works wherever you cloned the repo.
set -e
cd "$(dirname "$0")"

echo "📦 Marbaras Post — setup"
echo "------------------------"

# Pick a Python (3.10+ preferred; 3.9 works with the relaxed Django pin).
PY=$(command -v python3 || command -v python)
if [ -z "$PY" ]; then
  echo "❌ Python not found. Install Python from https://www.python.org/downloads/ and run again."
  read -r -p "Press Enter to close."
  exit 1
fi
echo "Using: $($PY --version)"

echo "→ Creating virtualenv..."
"$PY" -m venv venv

echo "→ Installing dependencies..."
./venv/bin/pip install --quiet --upgrade pip
./venv/bin/pip install --quiet -r requirements.txt

# Create a .env with safe SANDBOX credentials if one doesn't exist yet.
if [ ! -f .env ]; then
  echo "→ Writing .env (SANDBOX / test mode — nothing real, not billed)..."
  cat > .env <<'ENV'
GLOBAL_MAIL_API_KEY=T2Lnu62rspJ1wdaI3JOA1JpM7oECmfz2
GLOBAL_MAIL_API_SECRET=4AIqPAggU2aIPvPE
GLOBAL_MAIL_CUSTOMER_EKP=316276595
GLOBAL_MAIL_TEST_MODE=True
GLOBAL_MAIL_DISABLE_BUILTIN_PRODUCT_MAP=True
ENV
fi

echo "→ Setting up the database..."
./venv/bin/python manage.py migrate --no-input

echo ""
echo "→ Create your admin login (username + password):"
./venv/bin/python manage.py createsuperuser

echo ""
echo "✅ Done! Now double-click 'start.command' to launch the app."
read -r -p "Press Enter to close."
