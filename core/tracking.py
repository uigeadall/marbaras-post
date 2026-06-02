"""Minimal DHL / DPI Global Mail tracking client.

Auths with the same consumer key/secret model as the marbaras shop and reads
events from GET /dpi/tracking/v3/trackings/{barcode}. If no credentials are
configured it returns a friendly demo timeline so the site runs out of the box.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

_token_cache: Dict[str, Dict[str, Any]] = {}


def _host() -> str:
    test = bool(getattr(settings, "GLOBAL_MAIL_TEST_MODE", True))
    return "https://api-sandbox.dhl.com" if test else "https://api.dhl.com"


def _get_token() -> Optional[str]:
    key = getattr(settings, "GLOBAL_MAIL_API_KEY", "")
    secret = getattr(settings, "GLOBAL_MAIL_API_SECRET", "")
    if not (key and secret):
        return None
    cache_key = f"{_host()}|{key}"
    cached = _token_cache.get(cache_key)
    now = time.time()
    if cached and cached["expires_at"] > now + 60:
        return cached["token"]
    try:
        r = requests.get(
            f"{_host()}/dpi/v1/auth/accesstoken",
            auth=(key, secret),
            headers={"Accept": "application/json"},
            timeout=15,
        )
        if r.status_code != 200:
            logger.warning("DPI tracking auth %s: %s", r.status_code, r.text[:200])
            return None
        data = r.json()
        token = data.get("access_token")
        ttl = int(data.get("expires_in") or 14400)
        _token_cache[cache_key] = {"token": token, "expires_at": now + min(ttl, 14400)}
        return token
    except Exception:
        logger.exception("DPI tracking auth failed")
        return None


def _demo_timeline(barcode: str) -> Dict[str, Any]:
    return {
        "barcode": barcode,
        "status": "In transit",
        "demo": True,
        "events": [
            {"date": "2026-06-01 09:12", "status": "Shipment data received", "location": "Sofia, BG"},
            {"date": "2026-06-01 14:40", "status": "Inducted into network", "location": "Frankfurt, DE"},
            {"date": "2026-06-02 08:05", "status": "In transit", "location": "Frankfurt, DE"},
        ],
    }


def track(barcode: str) -> Optional[Dict[str, Any]]:
    """Return a normalized tracking dict, or None if not found."""
    barcode = (barcode or "").strip()
    if not barcode:
        return None

    token = _get_token()
    if not token:
        # No credentials configured yet → show a demo timeline so the UI works.
        return _demo_timeline(barcode)

    try:
        r = requests.get(
            f"{_host()}/dpi/tracking/v3/trackings/{barcode}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=20,
        )
    except Exception:
        logger.exception("DPI tracking request failed")
        return None
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        logger.warning("DPI tracking %s: %s", r.status_code, r.text[:300])
        return None

    data = r.json() or {}
    events: List[Dict[str, str]] = []
    # The DPI tracking payload nests events under items[].events — be lenient.
    raw_items = data.get("items") or data.get("trackings") or [data]
    for item in raw_items:
        for ev in item.get("events", []) or []:
            events.append(
                {
                    "date": ev.get("timestamp") or ev.get("date") or "",
                    "status": ev.get("status") or ev.get("description") or "",
                    "location": ev.get("location") or ev.get("country") or "",
                }
            )
    status = (raw_items[0].get("status") if raw_items else "") or (
        events[-1]["status"] if events else "Registered"
    )
    return {"barcode": barcode, "status": status, "demo": False, "events": events}
