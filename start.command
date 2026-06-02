#!/bin/bash
# Double-click to start Marbaras Post. Run setup.command first (once).
# Close this Terminal window to stop the server.
cd "$(dirname "$0")"

if [ ! -d venv ]; then
  echo "⚠️  Not set up yet — double-click 'setup.command' first."
  read -r -p "Press Enter to close."
  exit 1
fi

echo "🚀 Стартирам Marbaras Post..."
echo "   http://127.0.0.1:8099/app/   (затвори прозореца, за да спреш)"
( sleep 3 && open "http://127.0.0.1:8099/app/" ) &
exec ./venv/bin/python manage.py runserver 127.0.0.1:8099
