#!/usr/bin/env bash
# Render build step — install deps, collect static, apply migrations.
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate

# Free tier has no Shell — create the admin user from env vars instead.
# Reads DJANGO_SUPERUSER_USERNAME / _PASSWORD / _EMAIL. Idempotent.
if [ -n "$DJANGO_SUPERUSER_USERNAME" ] && [ -n "$DJANGO_SUPERUSER_PASSWORD" ]; then
  python manage.py createsuperuser --no-input || true
fi
