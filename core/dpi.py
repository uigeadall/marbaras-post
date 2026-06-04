"""DHL / DPI Global Mail label engine for Marbaras Post.

Ported from the marbaras shop's proven integration. Auths with consumer
key/secret, creates a finalized order (or an OPEN/prepared one), fetches the
4x6 label PDF and refits it onto an exact 4x6 page for thermal printers.
"""
from __future__ import annotations

import base64
import logging
import re
import time
from io import BytesIO
from typing import Any, Dict, List, Optional

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

_token_cache: Dict[str, Dict[str, Any]] = {}

# DPI contracts vary. GPT (Packet Tracked) is valid for most destinations;
# GPP only exists on some contracts. The built-in non-EU map can be disabled.
_BUILTIN_NON_EU_PRODUCT_MAP = {
    "US": "GPP", "CA": "GPP", "AU": "GPP", "GB": "GPP", "CH": "GPP",
    "NO": "GPP", "JP": "GPP", "CN": "GPP", "TR": "GPP",
}

# EU member states — no customs declaration / tax references are needed/printed.
_EU_COUNTRIES = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR",
    "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK",
    "SI", "ES", "SE",
}


def _host() -> str:
    test = bool(getattr(settings, "GLOBAL_MAIL_TEST_MODE", True))
    return "https://api-sandbox.dhl.com" if test else "https://api.dhl.com"


def _cfg(key, default=""):
    return getattr(settings, key, default)


def _http(method: str, url: str, **kwargs):
    """requests wrapper that never raises — returns the Response, or None on a
    network error (timeout / connection refused / DNS). Callers treat None as a
    failure, so a DHL outage yields a friendly message instead of a 500."""
    try:
        return requests.request(method, url, **kwargs)
    except requests.RequestException as exc:
        logger.error("DPI request failed: %s %s — %s", method, url, exc)
        return None


def get_token() -> Optional[str]:
    key, secret = _cfg("GLOBAL_MAIL_API_KEY"), _cfg("GLOBAL_MAIL_API_SECRET")
    if not (key and secret):
        return None
    ck = f"{_host()}|{key}"
    cached = _token_cache.get(ck)
    now = time.time()
    if cached and cached["expires_at"] > now + 60:
        return cached["token"]
    r = _http(
        "GET",
        f"{_host()}/dpi/v1/auth/accesstoken",
        auth=(key, secret),
        headers={"Accept": "application/json"},
        timeout=15,
    )
    if r is None or r.status_code != 200:
        if r is not None:
            logger.error("DPI auth %s: %s", r.status_code, r.text[:200])
        return None
    try:
        data = r.json()
    except ValueError:
        logger.error("DPI auth returned non-JSON")
        return None
    token = data.get("access_token")
    if not token:
        return None
    ttl = int(data.get("expires_in") or 14400)
    _token_cache[ck] = {"token": token, "expires_at": now + min(ttl, 14400)}
    return token


def resolve_product(country: str) -> str:
    default = _cfg("GLOBAL_MAIL_PRODUCT_CODE", "GPT") or "GPT"
    disable = str(_cfg("GLOBAL_MAIL_DISABLE_BUILTIN_PRODUCT_MAP", "")).lower() in (
        "1", "true", "yes", "on",
    )
    if disable or bool(_cfg("GLOBAL_MAIL_TEST_MODE", True)):
        return default
    return _BUILTIN_NON_EU_PRODUCT_MAP.get((country or "").upper(), default)


def _sanitize_phone(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    has_plus = s.startswith("+")
    s = re.sub(r"[^\d\s\.\-\(\)]", "", s)
    return (("+" + s.lstrip()) if has_plus else s).strip()[:25]


def _clean_text(value: str, maxlen: int) -> str:
    """Satisfy DPI's field pattern ``^(?![=\\-\\+@])[^?�]*$``: drop the
    replacement char and question marks, never start with = - + @ (DHL rejects
    those as injection risks), trim to maxlen."""
    s = (value or "").replace("?", "").replace("�", "")
    s = s.strip().lstrip("=-+@").strip()
    return s[:maxlen]


def build_item(shipment, *, finalize: bool = True) -> Dict[str, Any]:
    """Build the DPI item payload for one Shipment."""
    country = (shipment.country or "BG").upper()[:2]
    # Gross = the whole parcel (envelope + goods); net = the goods only (customs).
    gross = max(int(shipment.weight_g or 100), 10)
    try:
        net = int(getattr(shipment, "net_weight_g", 0) or 0)
    except (TypeError, ValueError):
        net = 0
    if net <= 0:
        net = gross               # default: declare net = gross
    net = max(min(net, gross), 1)  # net must be ≥1g and never exceed gross
    value = max(round(float(shipment.value or 1), 2), 1.0)
    product = (shipment.product or "").strip() or resolve_product(country)

    def _desc(d):
        # DHL needs the description 3–33 chars for cross-border products.
        d = _clean_text(d, 33)
        if len(d) < 3:
            d = (d + " goods").strip()[:33]
        return d or "Goods"

    hs_code = (getattr(shipment, "hs_code", "") or _cfg("GLOBAL_MAIL_HS_CODE", "711311"))
    origin = (getattr(shipment, "origin_country", "") or _cfg("SHOP_COUNTRY", "BG"))

    # Build one content piece per product line. contents_json (multiple
    # products) wins; otherwise fall back to the single legacy fields.
    rows = []
    for ln in (getattr(shipment, "contents_json", None) or []):
        if not isinstance(ln, dict) or not (ln.get("description") or "").strip():
            continue
        try:
            q = max(int(ln.get("quantity") or 1), 1)
        except (TypeError, ValueError):
            q = 1
        try:
            v = max(round(float(ln.get("value") or 1), 2), 1.0)
        except (TypeError, ValueError):
            v = 1.0
        try:
            nw = int(ln.get("net_weight") or 0)
        except (TypeError, ValueError):
            nw = 0
        rows.append({"description": _desc(ln["description"]), "quantity": q, "value": v, "net": nw})
    if not rows:
        rows = [{
            "description": _desc(shipment.description or "Goods"),
            "quantity": max(int(getattr(shipment, "quantity", 1) or 1), 1),
            "value": value,
            "net": net,
        }]

    # Net weights must each be ≥1 and sum to ≤ the parcel gross weight.
    nets = [r["net"] for r in rows]
    if sum(nets) <= 0 or sum(nets) > gross:
        each = max(gross // len(rows), 1)
        nets = [each] * len(rows)
    nets = [max(n, 1) for n in nets]

    contents = []
    total_value = 0.0
    for i, (r, nw) in enumerate(zip(rows, nets), 1):
        contents.append({
            "contentPieceAmount": r["quantity"],
            "contentPieceDescription": r["description"],
            "contentPieceHsCode": hs_code,
            "contentPieceOrigin": origin,
            "contentPieceValue": f'{r["value"]:.2f}',
            "contentPieceNetweight": nw,
        })
        total_value += r["value"]
    total_value = max(round(total_value, 2), 1.0)
    # Non-EU destinations require a recipient phone OR email. If the pasted
    # address has neither, fall back to a configured contact so it still books.
    phone = _sanitize_phone(shipment.recipient_phone)
    email = (shipment.recipient_email or "").strip()[:50]  # DPI max 50
    if not phone and not email:
        brand = _cfg("BRAND", {})
        email = (
            _cfg("GLOBAL_MAIL_FALLBACK_EMAIL", "")
            or (brand.get("email") if isinstance(brand, dict) else "")
            or "noreply@marbaras-post.local"
        )[:50]
    # serviceLevel must be PRIORITY or REGISTERED (the only values DPI accepts).
    service = (shipment.service_level or "PRIORITY").upper()
    if service not in ("PRIORITY", "REGISTERED"):
        service = "PRIORITY"
    return {
        "product": product,
        "serviceLevel": service,
        "recipient": _clean_text(shipment.recipient_name, 35) or "Recipient",
        "recipientPhone": phone,
        "recipientEmail": email,
        "addressLine1": _clean_text(shipment.address_line1, 40) or "Address",
        "addressLine2": _clean_text(shipment.address_line2, 40),
        "addressLine3": _clean_text(getattr(shipment, "address_line3", ""), 40),
        # Sender IOSS/VOEC: only for non-EU (empty for EU).
        "senderTaxId": "" if country in _EU_COUNTRIES else (getattr(shipment, "tax_id", "") or "").strip()[:35],
        # Importer customs reference: always sent when the operator fills it in
        # (manual field) — prints on the label regardless of destination.
        "importerTaxId": (getattr(shipment, "importer_tax_id", "") or "").strip()[:35],
        "city": _clean_text((shipment.city or "").rstrip(","), 30) or "City",
        "state": _clean_text(getattr(shipment, "state", ""), 30),
        "postalCode": ((shipment.postal_code or "").strip().upper()[:10]) or "0000",
        "destinationCountry": country,
        "shipmentAmount": total_value,
        "shipmentCurrency": (shipment.currency or "EUR").upper()[:3],
        "shipmentGrossWeight": gross,
        "shipmentNaturetype": getattr(shipment, "content_type", "") or "SALE_GOODS",
        "returnItemWanted": False,
        # Customer reference: the operator's own (Reference field). Falls back to
        # an internal Sxx only when left blank, so DHL always has a reference.
        "custRef": ((getattr(shipment, "reference", "") or "").strip()[:28] or f"S{shipment.pk}"),
        "contents": contents,
    }


def _order_payload(items: List[Dict], finalize: bool) -> Dict[str, Any]:
    # Use the first item's customer reference as the job reference so the label
    # shows the operator's own number instead of an auto MP-/FIN- timestamp.
    job_ref = (items[0].get("custRef") if items else "") or f"JOB-{int(time.time())}"
    return {
        "customerEkp": str(_cfg("GLOBAL_MAIL_CUSTOMER_EKP")),
        "orderStatus": "FINALIZE" if finalize else "OPEN",
        "paperwork": {
            "contactName": _cfg("BRAND", {}).get("company", "Marbaras Post")[:35]
            if isinstance(_cfg("BRAND", {}), dict)
            else "Marbaras Post",
            "jobReference": str(job_ref)[:17],
            "telephoneNumber": _cfg("BRAND", {}).get("phone", "+359888000000")
            if isinstance(_cfg("BRAND", {}), dict)
            else "+359888000000",
            "awbCopyCount": 1,
        },
        "items": items,
    }


def create_label(shipment, *, finalize: bool = True) -> Dict[str, Any]:
    """Create a DHL shipment for one Shipment and return result fields.

    Returns ``{ok, error, awb, barcode, item_id, order_id, status_code}``.
    """
    token = get_token()
    if not token:
        return {"ok": False, "error": "Missing/invalid GLOBAL_MAIL credentials."}
    if not _cfg("GLOBAL_MAIL_CUSTOMER_EKP"):
        return {"ok": False, "error": "GLOBAL_MAIL_CUSTOMER_EKP is not set."}

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    item = build_item(shipment, finalize=finalize)
    payload = _order_payload([item], finalize)

    # Product fallback — a failed POST creates nothing, so retrying is safe.
    candidates = [item["product"]] + [
        c for c in ("GPT", "GPP", "GMP", "GMT") if c != item["product"]
    ]
    r = None
    for cand in candidates:
        payload["items"][0]["product"] = cand
        r = _http("POST", f"{_host()}/dpi/shipping/v1/orders", json=payload, headers=headers, timeout=60)
        if r.status_code in (200, 201):
            break
        t = (r.text or "").lower()
        if not (r.status_code in (400, 422) and ("product" in t or "destination country is invalid" in t)):
            break

    if r is None or r.status_code not in (200, 201):
        return {
            "ok": False,
            "status_code": getattr(r, "status_code", None),
            "error": (r.text or "")[:400] if r is not None else "no response",
        }

    body = r.json() or {}
    order_id = body.get("orderId")
    shipments = body.get("shipments") or []
    items = body.get("items") or (shipments[0].get("items") if shipments else []) or []
    awb = shipments[0].get("awb") if shipments else None
    first = items[0] if items else {}
    return {
        "ok": True,
        "order_id": order_id,
        "awb": awb,
        "item_id": first.get("id"),
        "barcode": first.get("barcode"),
        "status_code": r.status_code,
    }


def create_order_for_many(shipments, *, finalize: bool) -> Dict[int, Dict[str, Any]]:
    """Bundle shipments into as few DPI orders as possible (grouped by
    destination country + service) so they share one AWB. Returns a map
    ``{shipment_pk: {ok, order_id, item_id, barcode, awb, error}}``.

    finalize=False → OPEN (lands in the DP shipment-preparation summary, one
    shared AWB once finalized). finalize=True → immediate AWB + labels.
    """
    out: Dict[int, Dict[str, Any]] = {}
    token = get_token()
    if not token or not _cfg("GLOBAL_MAIL_CUSTOMER_EKP"):
        for s in shipments:
            out[s.pk] = {"ok": False, "error": "Missing DHL credentials / EKP."}
        return out
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    # Group by (product, serviceLevel) — DHL puts all items with the same
    # product+service on ONE AWB, regardless of destination country. Grouping
    # by country (the old behaviour) wrongly split e.g. CH+DE into two orders.
    groups: Dict[tuple, list] = {}
    by_ref: Dict[str, Any] = {}
    for s in shipments:
        item = build_item(s, finalize=finalize)
        # Map responses by the custRef we actually send (the operator's
        # reference). Guarantee it's unique within this batch so the response
        # maps back to the right shipment even if two share a reference.
        ref = item["custRef"]
        if ref in by_ref:
            ref = f"{ref}-{s.pk}"[:28]
            item["custRef"] = ref
        by_ref[ref] = s
        key = (item["product"], item["serviceLevel"])
        groups.setdefault(key, []).append((s, item))

    for (product, service), pairs in groups.items():
        items = [it for _, it in pairs]
        payload = _order_payload(items, finalize)
        original = items[0]["product"]
        candidates = [original] + [c for c in ("GPT", "GPP", "GMP", "GMT") if c != original]
        r = None
        for cand in candidates:
            for it in items:
                it["product"] = cand
            r = _http("POST", f"{_host()}/dpi/shipping/v1/orders", json=payload, headers=headers, timeout=90)
            if r.status_code in (200, 201):
                break
            t = (r.text or "").lower()
            if not (r.status_code in (400, 422) and ("product" in t or "destination country is invalid" in t)):
                break
        if r is None or r.status_code not in (200, 201):
            err = (r.text or "")[:300] if r is not None else "no response"
            for s, _ in pairs:
                out[s.pk] = {"ok": False, "status_code": getattr(r, "status_code", None), "error": err}
            continue
        body = r.json() or {}
        order_id = body.get("orderId")
        shipments_resp = body.get("shipments") or []
        resp_items = body.get("items") or (shipments_resp[0].get("items") if shipments_resp else []) or []
        awb_for = {}
        for sh in shipments_resp:
            for it in sh.get("items") or []:
                awb_for[it.get("custRef")] = sh.get("awb")
        for it in resp_items:
            ref = it.get("custRef")
            s = by_ref.get(ref)
            if not s:
                continue
            out[s.pk] = {
                "ok": True,
                "order_id": order_id,
                "item_id": it.get("id"),
                "barcode": it.get("barcode"),
                "awb": awb_for.get(ref) or (shipments_resp[0].get("awb") if shipments_resp else None),
            }
    return out


def add_items_to_order(order_id: str, shipments) -> Dict[int, Dict[str, Any]]:
    """Add items to an existing OPEN order (so they share its AWB).

    DHL only allows this while the order is OPEN (in preparation) — a
    finalized order is locked. Returns ``{shipment_pk: {ok, item_id,
    barcode, error}}``.
    """
    out: Dict[int, Dict[str, Any]] = {}
    token = get_token()
    if not token:
        for s in shipments:
            out[s.pk] = {"ok": False, "error": "Missing DHL credentials."}
        return out
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    items = []
    by_ref = {}
    for s in shipments:
        item = build_item(s)
        ref = item["custRef"]
        if ref in by_ref:
            ref = f"{ref}-{s.pk}"[:28]
            item["custRef"] = ref
        items.append(item)
        by_ref[ref] = s

    # The add-items endpoint takes a JSON array of items. Product fallback in
    # case the resolved product is rejected for a destination.
    original = items[0]["product"] if items else "GPT"
    candidates = [original] + [c for c in ("GPT", "GPP", "GMP", "GMT") if c != original]
    url = f"{_host()}/dpi/shipping/v1/orders/{order_id}/items"
    r = None
    for cand in candidates:
        for it in items:
            it["product"] = cand
        r = _http("POST", url, json=items, headers=headers, timeout=60)
        if r.status_code in (200, 201):
            break
        t = (r.text or "").lower()
        if not (r.status_code in (400, 422) and ("product" in t or "destination country is invalid" in t)):
            break

    if r is None or r.status_code not in (200, 201):
        err = (r.text or "")[:300] if r is not None else "no response"
        for s in shipments:
            out[s.pk] = {"ok": False, "status_code": getattr(r, "status_code", None), "error": err}
        return out

    body = r.json() or {}
    # Response may be a bare list, {"items": [...]}, or {"shipments":[{"items":[...]}]}.
    if isinstance(body, list):
        resp_items = body
    else:
        resp_items = body.get("items") or (
            (body.get("shipments") or [{}])[0].get("items") or []
        )
    for it in resp_items:
        s = by_ref.get(it.get("custRef"))
        if s:
            out[s.pk] = {
                "ok": True,
                "item_id": it.get("id"),
                "barcode": it.get("barcode"),
            }
    # Anything that didn't come back → mark failed so the UI is honest.
    for s in shipments:
        out.setdefault(s.pk, {"ok": False, "error": "not returned by DHL"})
    return out


def finalize_order(order_id: str, job_ref: str = "") -> Dict[str, Any]:
    """Finalize an OPEN order → assigns AWB + labels. Returns
    ``{ok, awb_by_ref, barcode_by_ref, error}``."""
    token = get_token()
    if not token:
        return {"ok": False, "error": "no token"}
    brand = _cfg("BRAND", {})
    paperwork = {
        "contactName": (brand.get("company") if isinstance(brand, dict) else "Marbaras Post")[:35],
        "jobReference": (str(job_ref) or f"JOB-{int(time.time())}")[:17],
        "telephoneNumber": (brand.get("phone") if isinstance(brand, dict) else "+359888000000"),
        "awbCopyCount": 1,
    }
    r = _http(
        "POST",
        f"{_host()}/dpi/shipping/v1/orders/{order_id}/finalization",
        json=paperwork,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json"},
        timeout=90,
    )
    if r is None:
        return {"ok": False, "error": "DHL unreachable (network error)."}
    if r.status_code not in (200, 201):
        return {"ok": False, "status_code": r.status_code, "error": (r.text or "")[:300]}
    body = r.json() or {}
    shipments = body.get("shipments") or []
    # Map by DHL item id (stable, set at create) rather than custRef, so the
    # operator's free-text reference can't affect result mapping.
    fallback_awb = shipments[0].get("awb") if shipments else None
    awb_by_id, barcode_by_id = {}, {}
    for sh in shipments:
        for it in sh.get("items") or []:
            iid = str(it.get("id"))
            awb_by_id[iid] = sh.get("awb")
            barcode_by_id[iid] = it.get("barcode")
    return {"ok": True, "awb": fallback_awb, "awb_by_id": awb_by_id, "barcode_by_id": barcode_by_id}


def delete_item(item_id: str) -> Dict[str, Any]:
    """Delete an OPEN item (cancel). Finalized items return 422/404."""
    token = get_token()
    if not token or not item_id:
        return {"ok": False, "error": "no token/item"}
    r = _http(
        "DELETE",
        f"{_host()}/dpi/shipping/v1/items/{item_id}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=30,
    )
    if r is None:
        return {"ok": False, "error": "DHL unreachable (network error)."}
    if r.status_code in (200, 204):
        return {"ok": True}
    t = (r.text or "").lower()
    return {
        "ok": False,
        "finalized": r.status_code in (404, 422) and ("finalized" in t or "cannot be found" in t),
        "status_code": r.status_code,
        "error": (r.text or "")[:200],
    }


def get_item_label(item_id: str) -> Optional[bytes]:
    """Fetch one item's 4x6 label PDF (refit), or None."""
    token = get_token()
    if not token or not item_id:
        return None
    r = _http(
        "GET",
        f"{_host()}/dpi/shipping/v1/items/{item_id}/label",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/pdf"},
        params={"pageSize": "4x6"},
        timeout=30,
    )
    if r is None or r.status_code != 200 or not r.content:
        logger.error("DPI item label %s: %s", r.status_code, (r.text or "")[:200])
        return None
    return refit_pdf_to_4x6(r.content)


def get_awb_paperwork(awb: str) -> Optional[bytes]:
    """The AWB dispatch document (DHL portal step 3 'Print paperwork').
    GET /shipments/{awb}/awblabels → PDF you hand to DHL at pickup."""
    token = get_token()
    if not token or not awb:
        return None
    r = _http(
        "GET",
        f"{_host()}/dpi/shipping/v1/shipments/{awb}/awblabels",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/pdf"},
        timeout=60,
    )
    if r is None or r.status_code != 200 or not r.content:
        logger.error("DPI awb paperwork %s: %s", r.status_code, (r.text or "")[:200])
        return None
    return r.content


def get_item_labels_for_awb(awb: str) -> Optional[bytes]:
    """All labels for one AWB as a single 4x6 PDF."""
    token = get_token()
    if not token or not awb:
        return None
    r = _http(
        "GET",
        f"{_host()}/dpi/shipping/v1/shipments/{awb}/itemlabels",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/pdf+singlepage+6x4",
        },
        timeout=60,
    )
    if r is None or r.status_code != 200 or not r.content:
        return None
    return r.content


def get_item_zpl(item_id: str) -> Optional[bytes]:
    """ZPL for a single item — works for OPEN/prepared items too (no AWB
    needed), so labels can be shown/printed before finalizing."""
    token = get_token()
    if not token or not item_id:
        return None
    r = _http(
        "GET",
        f"{_host()}/dpi/shipping/v1/items/{item_id}/label",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/zpl+6x4"},
        timeout=30,
    )
    if r is None or r.status_code != 200 or not r.content:
        return None
    return r.content


def render_zpl_to_pdf(zpl: bytes, dpmm: int = 8) -> Optional[bytes]:
    """Render ZPL to a crisp 6x4 PDF via Labelary, so the barcode (drawn from
    the ZPL ^BC command) prints sharp through a normal printer driver — instead
    of DHL's low-res raster barcode. Returns PDF bytes or None.

    Note: the label content (incl. address) is sent to api.labelary.com for
    rendering. Set LABELARY_URL to a self-hosted instance to avoid that.
    """
    if not zpl:
        return None
    base = _cfg("LABELARY_URL", "https://api.labelary.com")
    try:
        r = requests.post(
            f"{base}/v1/printers/{dpmm}dpmm/labels/6x4/",
            headers={"Accept": "application/pdf"},
            data=zpl,
            timeout=60,
        )
    except requests.RequestException as exc:
        logger.error("Labelary render failed: %s", exc)
        return None
    if r.status_code != 200 or r.content[:4] != b"%PDF":
        logger.error("Labelary %s: %s", r.status_code, (r.text or "")[:200])
        return None
    return r.content


def get_labels_zpl_for_awb(awb: str, rotated: bool = False) -> Optional[bytes]:
    """All labels for one AWB as ZPL (for thermal printers like the Zebra
    ZP 505). The printer renders the barcode natively at its full DPI, so it
    comes out perfectly crisp — unlike the low-resolution raster barcode DHL
    embeds in the PDF."""
    token = get_token()
    if not token or not awb:
        return None
    accept = "application/zpl+rotated+6x4" if rotated else "application/zpl+6x4"
    r = _http(
        "GET",
        f"{_host()}/dpi/shipping/v1/shipments/{awb}/itemlabels",
        headers={"Authorization": f"Bearer {token}", "Accept": accept},
        timeout=60,
    )
    if r is None or r.status_code != 200 or not r.content:
        return None
    return r.content


def refit_pdf_to_4x6(pdf_bytes: bytes, margin_pt: float = 6.0) -> bytes:
    """Return the DHL label at its natural size so it fills the whole page.

    DPI already returns a label-sized PDF (e.g. ~281x298 pt). Forcing it onto a
    fixed 4x6 portrait page just left the label in the top half with empty space
    below — which is the "label on half the page" problem. So:

      * If the PDF is already label-sized → return it unchanged (fills its page).
      * Only if DHL returns a big A4/A5 sheet (label in the top half, rest
        receipt/blank) do we crop the top half out and return that.
    """
    try:
        from pypdf import PdfReader, PdfWriter, Transformation
        from pypdf.generic import RectangleObject

        reader = PdfReader(BytesIO(pdf_bytes))
        if not reader.pages:
            return pdf_bytes
        src = reader.pages[0]

        box = None
        for attr in ("trimbox", "cropbox", "mediabox"):
            b = getattr(src, attr, None)
            if b is not None and float(b.width) > 0 and float(b.height) > 0:
                box = b
                break
        if box is None:
            return pdf_bytes
        bw, bh = float(box.width), float(box.height)

        # A real label is at most ~6.5 inch (470 pt) on a side. Anything larger
        # is an A4/A5 sheet → crop the label out of the top half. Otherwise the
        # PDF is already label-sized: leave it as-is so it fills the page.
        BIG = 470.0
        if bw <= BIG and bh <= BIG:
            return pdf_bytes

        x0, y0 = float(box.left), float(box.bottom)
        y0 = y0 + bh / 2.0   # keep the top half (the label)
        bh = bh / 2.0
        writer = PdfWriter()
        page = writer.add_blank_page(width=bw, height=bh)
        page.merge_transformed_page(src, Transformation().translate(-x0, -y0))
        for attr in ("mediabox", "cropbox", "trimbox"):
            setattr(page, attr, RectangleObject((0, 0, bw, bh)))
        out = BytesIO()
        writer.write(out)
        return out.getvalue()
    except Exception as exc:
        logger.warning("refit_pdf_to_4x6 failed: %s", exc)
        return pdf_bytes
