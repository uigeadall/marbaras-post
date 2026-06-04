"""Smart paste parser: turn a copy-pasted address block (or several) into
shipment dicts. Ported/adapted from the marbaras shop.

Supported input — one block per parcel, blank line between parcels:

    Anita Leuenberger
    Hauptstrasse 14
    4492 Tecknau
    Switzerland
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

_COUNTRY_NAME_TO_ISO2 = {
    "bulgaria": "BG", "germany": "DE", "deutschland": "DE",
    "united kingdom": "GB", "great britain": "GB", "uk": "GB", "england": "GB",
    "switzerland": "CH", "schweiz": "CH", "austria": "AT", "france": "FR",
    "italy": "IT", "italia": "IT", "spain": "ES", "españa": "ES",
    "netherlands": "NL", "holland": "NL", "belgium": "BE", "poland": "PL",
    "greece": "GR", "romania": "RO", "ireland": "IE", "portugal": "PT",
    "sweden": "SE", "denmark": "DK", "norway": "NO", "finland": "FI",
    "czechia": "CZ", "czech republic": "CZ", "hungary": "HU", "croatia": "HR",
    "usa": "US", "united states": "US", "u.s.a.": "US", "america": "US",
    "canada": "CA", "australia": "AU", "japan": "JP", "turkey": "TR",
}

_UK_POSTCODE_RE = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})\s*$", re.I)


def normalize_country(raw: str) -> str:
    v = (raw or "").strip()
    if not v:
        return ""
    if len(v) == 2 and v.isalpha():
        return v.upper()
    return _COUNTRY_NAME_TO_ISO2.get(v.lower(), v[:2].upper())


def split_postal_city(line: str) -> Tuple[str, str]:
    s = (line or "").strip()
    if not s:
        return "", ""
    m = _UK_POSTCODE_RE.search(s)
    if m:
        return re.sub(r"\s+", " ", m.group(1).upper()), s[: m.start()].strip().rstrip(",").strip()
    parts = s.split()
    if any(c.isdigit() for c in parts[0]):
        return parts[0], " ".join(parts[1:]).strip().rstrip(",").strip()
    if any(c.isdigit() for c in parts[-1]):
        return parts[-1], " ".join(parts[:-1]).strip().rstrip(",").strip()
    return "", s


def _is_phone(line: str) -> bool:
    """A line that is a phone number: only +/digits/spaces/()-./ and >=7 digits."""
    s = (line or "").strip()
    if not re.match(r"^[\+\(]?[\d][\d\s()\-./]*$", s):
        return False
    return len(re.sub(r"\D", "", s)) >= 7


def _is_email(line: str) -> bool:
    s = (line or "").strip()
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", s))


def _country_iso(line: str):
    """ISO2 if the line names a country (or is a 2-letter code), else None."""
    v = (line or "").strip()
    if len(v) == 2 and v.isalpha():
        return v.upper()
    return _COUNTRY_NAME_TO_ISO2.get(v.lower())


def parse_blocks(text: str) -> List[Dict[str, Any]]:
    """Parse one or more pasted address blocks into shipment dicts.

    Phone, email and country are detected wherever they appear in the block
    (not just by line position), so trailing phone numbers, a country in the
    middle, etc. all parse correctly.
    """
    results: List[Dict[str, Any]] = []
    for block in re.split(r"\n\s*\n", (text or "").strip()):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue

        phone = email = country = ""
        kept: List[str] = []
        for ln in lines:
            if not phone and _is_phone(ln):
                phone = ln.strip()
                continue
            if not email and _is_email(ln):
                email = ln.strip()
                continue
            iso = _country_iso(ln)
            if not country and iso:
                country = iso
                continue
            kept.append(ln)

        if not kept:
            continue
        name = kept[0]
        middle = kept[1:]

        # Find the lowest line that contains a digit — that's the postal line.
        pc_idx, postal, city = None, "", ""
        for i in range(len(middle) - 1, -1, -1):
            if any(c.isdigit() for c in middle[i]):
                pc_idx = i
                postal, city = split_postal_city(middle[i])
                break

        if pc_idx is not None:
            # Case A: the postal line is JUST a postal code (e.g. "71409"),
            # so the city is the line ABOVE it (Amazon layout: city / postal).
            if not city and pc_idx - 1 >= 0:
                city = middle[pc_idx - 1].strip()
                street_lines = middle[: pc_idx - 1]
            else:
                # Case B: postal + city on the same line (e.g. "4492 Tecknau").
                street_lines = middle[:pc_idx]
        else:
            street_lines = middle

        results.append({
            "recipient_name": name,
            "recipient_phone": phone,
            "recipient_email": email,
            "address_line1": street_lines[0] if street_lines else "",
            "address_line2": " ".join(street_lines[1:]).strip() if len(street_lines) > 1 else "",
            "city": city,
            "postal_code": postal,
            "country": country or "BG",
        })
    return results
