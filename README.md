# Marbaras Post

An a1post.bg-style courier platform — public marketing site + parcel tracking,
with a Django backend ready to grow into the full shipment-management platform
(accounts, label creation, combined AWB dispatch) reusing the DHL/DPI engine.

## Run it locally

```bash
cd ~/marbaras-post
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/python manage.py migrate
./venv/bin/python manage.py runserver
```

Then open http://127.0.0.1:8000/

- `/`            — landing page (BG default, `?lang=en` for English)
- `/track/`      — parcel tracking (demo timeline until DHL creds are set)
- `/admin/`      — Django admin (`createsuperuser` first)

## Make it yours

Everything brand-related lives in `marbaras_post/settings.py → BRAND`
(name, phone, email, company, address, hours) and the copy in
`core/content.py` (BG/EN). Colors are CSS variables at the top of
`static/css/styles.css`.

## Connect real DHL/DPI tracking

Set these env vars (same as the marbaras shop):

```
GLOBAL_MAIL_API_KEY=...
GLOBAL_MAIL_API_SECRET=...
GLOBAL_MAIL_CUSTOMER_EKP=316276595
GLOBAL_MAIL_TEST_MODE=False
```

Without them, tracking shows a demo timeline so the UI always works.

## Roadmap (phases)

1. ✅ Landing page (1:1 a1post-style) + parcel tracking — **done**
2. Accounts & customer dashboard (`/app/`) — register, login, address book
3. Shipment creation — paste/import address → label (port DPI engine from marbaras)
4. Combined AWB dispatch + bulk label PDF + manifest
5. Pricing/rate calculator, returns, billing
