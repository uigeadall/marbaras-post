import json
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import dpi, parsing, tracking
from .content import PRODUCTS, SERVICE_LEVELS
from .countries import COUNTRIES
from .models import Shipment


# ---------------------------------------------------------------- public site
def landing(request):
    return render(request, "landing.html")


def track(request):
    barcode = (request.GET.get("number") or "").strip()
    result = tracking.track(barcode) if barcode else None
    return render(
        request, "track.html",
        {"barcode": barcode, "result": result, "searched": bool(barcode)},
    )


# ---------------------------------------------------------------- app (operator)
@login_required
def dashboard(request):
    qs = Shipment.objects.all()
    flt = request.GET.get("status", "active")
    # AWB filter: clicking an AWB shows only that AWB's parcels.
    awb_filter = (request.GET.get("awb") or "").strip()
    if awb_filter:
        shipments = qs.filter(awb=awb_filter)[:200]
    else:
        tabs = {
            # Active = work in progress; finalized (label_created) move to Приключени.
            "active": qs.exclude(status__in=["shipped", "cancelled", "label_created"]),
            "draft": qs.filter(status="draft"),
            "prepared": qs.filter(status="prepared"),
            "label_created": qs.filter(status="label_created"),
            "all": qs,
        }
        shipments = tabs.get(flt, tabs["active"])[:200]

    # Open preparation orders (status=prepared, no AWB yet) — targets you can
    # still add more parcels to. Grouped by DPI order id.
    open_orders = []
    seen = {}
    for s in qs.filter(status="prepared", awb="").exclude(dpi_order_id=""):
        seen.setdefault(s.dpi_order_id, []).append(s)
    for oid, group in seen.items():
        sample = group[0]
        open_orders.append({
            "order_id": oid,
            "count": len(group),
            "label": f"Batch #{oid} · {len(group)} parcels · {sample.city}, {sample.country}",
        })

    counts = {
        "active": qs.exclude(status__in=["shipped", "cancelled", "label_created"]).count(),
        "draft": qs.filter(status="draft").count(),
        "prepared": qs.filter(status="prepared").count(),
        "label_created": qs.filter(status="label_created").count(),
        "all": qs.count(),
    }
    return render(
        request, "app/dashboard.html",
        {
            "shipments": shipments,
            "countries": COUNTRIES,
            "content_types": Shipment.CONTENT_TYPE_CHOICES,
            "products": PRODUCTS,
            "service_levels": SERVICE_LEVELS,
            "current_status": flt,
            "counts": counts,
            "open_orders": open_orders,
            "awb_filter": awb_filter,
        },
    )


@login_required
@require_POST
def parse_address_ajax(request):
    """Parse a pasted address block → JSON fields, to auto-fill the form."""
    raw = request.POST.get("pasted", "")
    blocks = parsing.parse_blocks(raw)
    if not blocks:
        return JsonResponse({"ok": False})
    return JsonResponse({"ok": True, **blocks[0]})


@login_required
@require_POST
def new_shipment(request):
    """Create ONE shipment (a1post 'Нова пратка') from the detailed form.
    Stays a draft ('Unsent') — dispatch it later via Combine → Finalize."""
    g = request.POST.get
    name = (g("recipient_name") or "").strip()
    if not name:
        messages.error(request, "Name is required.")
        return redirect("dashboard")
    phone = (g("recipient_phone") or "").strip()
    email = (g("recipient_email") or "").strip()
    if not phone and not email:
        messages.error(request, "Enter a recipient phone OR email (at least one is required).")
        return redirect("dashboard")

    # Customs product lines (cp_* arrays — one row per product).
    descs = request.POST.getlist("cp_description")
    qtys = request.POST.getlist("cp_quantity")
    netws = request.POST.getlist("cp_netweight")
    vals = request.POST.getlist("cp_value")
    lines = []
    for i, d in enumerate(descs):
        d = (d or "").strip()
        if not d:
            continue
        lines.append({
            "description": d[:64],
            "quantity": int(qtys[i]) if i < len(qtys) and (qtys[i] or "").isdigit() else 1,
            "net_weight": int(netws[i]) if i < len(netws) and (netws[i] or "").isdigit() else 0,
            "value": str(_dec(vals[i], "1")) if i < len(vals) and vals[i] else "1",
        })
    # Primary fields for the list view come from the first line (or a default).
    first = lines[0] if lines else {"description": "Goods", "quantity": 1, "value": "1", "net_weight": 0}
    total_value = sum(_dec(l["value"], "1") for l in lines) if lines else Decimal("1")

    s = Shipment.objects.create(
        owner=request.user,
        reference=(g("reference") or "").strip(),
        recipient_name=name,
        recipient_email=email,
        recipient_phone=phone,
        address_line1=(g("address_line1") or "").strip(),
        address_line2=(g("address_line2") or "").strip(),
        address_line3=(g("address_line3") or "").strip(),
        city=(g("city") or "").strip(),
        state=(g("state") or "").strip(),
        postal_code=(g("postal_code") or "").strip(),
        country=(g("country") or "BG").strip().upper()[:2],
        product=(g("product") or "").strip(),
        service_level=(g("service_level") or "PRIORITY").strip(),
        description=first["description"],
        quantity=first["quantity"],
        weight_g=int(g("weight_g")) if (g("weight_g") or "").isdigit() else 100,
        net_weight_g=int(first["net_weight"]),
        value=total_value,
        currency=(g("currency") or "EUR").upper()[:3],
        content_type=(g("content_type") or "SALE_GOODS"),
        hs_code=(g("hs_code") or "711311").strip(),
        origin_country=(g("origin_country") or "BG").strip().upper()[:2],
        tax_id=(g("tax_id") or "").strip(),
        importer_tax_id=(g("importer_tax_id") or "").strip(),
        contents_json=lines,
    )
    messages.success(request, f"✅ Shipment #{s.pk} created (Unsent). Select it → 🚀 Dispatch to create the label.")
    return redirect("dashboard")


def _dec(val, default="0"):
    try:
        return Decimal(str(val).replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)


@login_required
@require_POST
def paste(request):
    raw = request.POST.get("pasted", "")
    blocks = parsing.parse_blocks(raw)
    if not blocks:
        messages.error(
            request,
            "Could not read an address. Paste it as: name / street / postal code + city / country (one per line).",
        )
        return redirect("dashboard")

    weight = request.POST.get("weight_g") or ""
    value = request.POST.get("value") or ""
    currency = (request.POST.get("currency") or "EUR").upper()[:3]
    description = request.POST.get("description") or "Goods"
    phone = (request.POST.get("phone") or "").strip()
    email = (request.POST.get("email") or "").strip()
    make_label = request.POST.get("make_label") == "on"

    created = 0
    labelled = 0
    for b in blocks:
        s = Shipment.objects.create(
            owner=request.user,
            recipient_name=b["recipient_name"],
            recipient_phone=phone,
            recipient_email=email,
            address_line1=b["address_line1"],
            address_line2=b["address_line2"],
            city=b["city"],
            postal_code=b["postal_code"],
            country=b["country"],
            description=description,
            weight_g=int(weight) if str(weight).isdigit() else 100,
            value=_dec(value, "1") if value else Decimal("1"),
            currency=currency,
        )
        created += 1
        if make_label:
            res = dpi.create_label(s, finalize=True)
            if res.get("ok"):
                s.dpi_order_id = str(res.get("order_id") or "")
                s.dpi_item_id = str(res.get("item_id") or "")
                s.awb = str(res.get("awb") or "")
                s.tracking_number = str(res.get("barcode") or res.get("awb") or "")
                s.status = "label_created"
                s.label_created_at = timezone.now()
                s.save()
                labelled += 1
            else:
                s.status = "failed"
                s.notes = f"HTTP {res.get('status_code')}: {res.get('error')}"
                s.save(update_fields=["status", "notes"])

    msg = f"✅ Създадени {created} пратка(и)."
    if make_label:
        msg += f" {labelled} етикет(а) генерирани."
    messages.success(request, msg)
    return redirect("dashboard")


@login_required
@require_POST
def create_label(request, pk):
    s = get_object_or_404(Shipment, pk=pk)
    if s.dpi_item_id:
        messages.info(request, f"Shipment #{s.pk} already has a label.")
        return redirect("dashboard")
    res = dpi.create_label(s, finalize=True)
    if res.get("ok"):
        s.dpi_order_id = str(res.get("order_id") or "")
        s.dpi_item_id = str(res.get("item_id") or "")
        s.awb = str(res.get("awb") or "")
        s.tracking_number = str(res.get("barcode") or res.get("awb") or "")
        s.status = "label_created"
        s.label_created_at = timezone.now()
        s.save()
        messages.success(request, f"🖨️ Label created for #{s.pk} — tracking {s.tracking_number}.")
    else:
        s.status = "failed"
        s.notes = f"HTTP {res.get('status_code')}: {res.get('error')}"
        s.save(update_fields=["status", "notes"])
        messages.error(request, f"#{s.pk} failed: {(res.get('error') or '')[:160]}")
    return redirect("dashboard")


@login_required
def preview_draft(request, pk):
    """Local label preview for a DRAFT (or any shipment without a DHL item).

    Renders how the label will look from the shipment's own data — no DHL
    call, nothing created or billed. The real barcode/AWB only appear after
    you dispatch.
    """
    from django.conf import settings

    s = get_object_or_404(Shipment, pk=pk)
    return render(
        request, "app/label_preview.html",
        {"s": s, "brand": settings.BRAND},
    )


@login_required
def print_label(request, pk):
    s = get_object_or_404(Shipment, pk=pk)
    if not s.dpi_item_id:
        return HttpResponse("No label yet — create it first.", status=400)
    pdf = dpi.get_item_label(s.dpi_item_id)
    if not pdf:
        return HttpResponse("Label fetch failed — see logs.", status=502)
    resp = HttpResponse(pdf, content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="label-{s.pk}.pdf"'
    return resp


# ---------------------------------------------------------------- batch (AWB) flow
def _selected(request):
    ids = request.POST.getlist("ids")
    return list(Shipment.objects.filter(pk__in=ids).order_by("id"))


def _do_combine(shipments):
    """Create OPEN prep orders for the given draft shipments. Returns
    ``(prepared, failed, order_ids)``."""
    sel = [s for s in shipments if not s.dpi_item_id]
    if not sel:
        return 0, 0, set()
    results = dpi.create_order_for_many(sel, finalize=False)
    prepared = failed = 0
    order_ids = set()
    for s in sel:
        res = results.get(s.pk, {})
        if res.get("ok"):
            s.dpi_order_id = str(res.get("order_id") or "")
            s.dpi_item_id = str(res.get("item_id") or "")
            s.tracking_number = str(res.get("barcode") or "")
            s.status = "prepared"
            s.notes = f"In DP preparation — order #{s.dpi_order_id}"
            s.save()
            prepared += 1
            if s.dpi_order_id:
                order_ids.add(s.dpi_order_id)
        else:
            s.status = "failed"
            s.notes = f"Combine HTTP {res.get('status_code')}: {res.get('error')}"
            s.save(update_fields=["status", "notes"])
            failed += 1
    return prepared, failed, order_ids


def _do_finalize(order_ids):
    """Finalize the given DPI orders → assign AWB + labels. Returns
    ``(done, failed)``. Updates every sibling in each order."""
    done = failed = 0
    for oid in order_ids:
        siblings = list(Shipment.objects.filter(dpi_order_id=oid))
        # Job reference on the label = the operator's own reference.
        first = siblings[0] if siblings else None
        job_ref = (first.reference or f"S{first.pk}") if first else ""
        res = dpi.finalize_order(oid, job_ref=job_ref)
        if not res.get("ok"):
            for s in siblings:
                s.status = "failed"
                s.notes = f"Finalize HTTP {res.get('status_code')}: {res.get('error')}"
                s.save(update_fields=["status", "notes"])
                failed += 1
            continue
        for s in siblings:
            iid = str(s.dpi_item_id)
            s.awb = str(res.get("awb_by_id", {}).get(iid) or res.get("awb") or "")
            if res.get("barcode_by_id", {}).get(iid):
                s.tracking_number = str(res["barcode_by_id"][iid])
            s.status = "label_created"
            s.label_created_at = timezone.now()
            s.notes = ""
            s.save()
            done += 1
    return done, failed


@login_required
@require_POST
def combine(request):
    """Bundle selected drafts into ONE prep order → shared AWB after finalize."""
    sel = _selected(request)
    if not [s for s in sel if not s.dpi_item_id]:
        messages.error(request, "Select draft shipments (without a label) to batch.")
        return redirect("dashboard")
    prepared, failed, _ = _do_combine(sel)
    if prepared:
        messages.success(request, f"📦 {prepared} shipment(s) sent to preparation. Now Finalize for the shared AWB + labels.")
    if failed:
        messages.error(request, f"{failed} failed — see the notes.")
    return redirect("dashboard")


@login_required
@require_POST
def finalize(request):
    """Finalize selected prepared shipments → assign AWB + labels."""
    sel = [s for s in _selected(request) if s.dpi_order_id and not s.awb]
    if not sel:
        messages.error(request, "Select prepared shipments to finalize.")
        return redirect("dashboard")
    done, failed = _do_finalize({s.dpi_order_id for s in sel})
    if done:
        messages.success(request, f"✅ Finalized {done} shipment(s). Print the labels below.")
    if failed:
        messages.error(request, f"{failed} could not be finalized — see the notes.")
    return redirect("dashboard")


@login_required
@require_POST
def add_to_batch(request):
    """Add selected drafts to an existing OPEN preparation order, so they
    share that order's AWB once it's finalized."""
    order_id = (request.POST.get("target_order") or "").strip()
    drafts = [s for s in _selected(request) if not s.dpi_item_id]
    if not order_id:
        messages.error(request, "Choose which batch to add to (from the dropdown).")
        return redirect("dashboard")
    if not drafts:
        messages.error(request, "Select draft shipments to add to the batch.")
        return redirect("dashboard")
    # Safety: the target order must still be OPEN (no AWB locally).
    if Shipment.objects.filter(dpi_order_id=order_id).exclude(awb="").exists():
        messages.error(request, "This batch is already finalized — cannot add to it. Finalize the new ones as a new batch.")
        return redirect("dashboard")
    res = dpi.add_items_to_order(order_id, drafts)
    added = failed = 0
    for s in drafts:
        r = res.get(s.pk, {})
        if r.get("ok"):
            s.dpi_order_id = str(order_id)
            s.dpi_item_id = str(r.get("item_id") or "")
            s.tracking_number = str(r.get("barcode") or "")
            s.status = "prepared"
            s.notes = f"Added to batch #{order_id}"
            s.save()
            added += 1
        else:
            s.status = "failed"
            s.notes = f"Add failed: {r.get('error')}"
            s.save(update_fields=["status", "notes"])
            failed += 1
    if added:
        messages.success(request, f"➕ Added {added} shipment(s) to batch #{order_id} (same AWB). Finalize when ready.")
    if failed:
        messages.error(request, f"{failed} could not be added — see the notes.")
    return redirect("dashboard")


@login_required
@require_POST
def remove_from_batch(request):
    """Take selected PREPARED (not yet finalized) shipments out of their batch.

    Deletes the item from the open DHL order and resets the shipment back to
    a Draft, so it can be re-batched later. Lets you finalize the rest of the
    batch without it. Finalized shipments (with an AWB) can't be removed.
    """
    sel = _selected(request)
    removed = locked = skipped = 0
    for s in sel:
        if s.awb:  # already finalized — can't pull out
            locked += 1
            continue
        if s.status != "prepared" or not s.dpi_item_id:
            skipped += 1
            continue
        res = dpi.delete_item(s.dpi_item_id)
        if res.get("ok"):
            s.status = "draft"
            s.dpi_order_id = ""
            s.dpi_item_id = ""
            s.tracking_number = ""
            s.awb = ""
            s.notes = "Removed from batch — back to Draft."
            s.save()
            removed += 1
        else:
            s.notes = f"Remove failed: {res.get('error')}"
            s.save(update_fields=["notes"])
            skipped += 1
    if removed:
        messages.success(request, f"↩️ Removed {removed} shipment(s) from the batch — they're Drafts again.")
    if locked:
        messages.warning(request, f"{locked} already have an AWB (finalized) — can't be removed.")
    if skipped:
        messages.info(request, f"{skipped} skipped (not in a batch or failed).")
    return redirect("dashboard")


@login_required
@require_POST
def dispatch(request):
    """One-click: combine + finalize the selected drafts into one shared AWB
    with printable labels. The easy path — does both steps at once."""
    sel = _selected(request)
    drafts = [s for s in sel if not s.dpi_item_id]
    if not drafts:
        messages.error(request, "Select draft shipments to dispatch.")
        return redirect("dashboard")
    prepared, cfailed, order_ids = _do_combine(drafts)
    done = ffailed = 0
    if order_ids:
        done, ffailed = _do_finalize(order_ids)
    if done:
        messages.success(
            request,
            f"🚀 Изпратени {done} пратка(и) на един общ AWB — етикетите са "
            f"готови. Маркирай ги и натисни „Принтирай етикети“.",
        )
    if cfailed or ffailed:
        messages.error(request, f"{cfailed + ffailed} shipment(s) failed — see their notes.")
    return redirect("dashboard")


@login_required
@require_POST
def print_all(request):
    """One PDF with every selected label, merged. Fetches each shipment's own
    4x6 item label (same as the single Print button) so the bulk PDF is
    consistent and upright — not the landscape bulk-AWB format."""
    sel = _selected(request)
    items = [s for s in sel if s.dpi_item_id]
    if not items:
        messages.error(request, "The selected shipments have no label yet — finalize first.")
        return redirect("dashboard")
    pdfs = [p for p in (dpi.get_item_label(s.dpi_item_id) for s in items) if p]
    if not pdfs:
        messages.error(request, "Could not fetch the labels — see the logs.")
        return redirect("dashboard")
    if len(pdfs) == 1:
        out = pdfs[0]
    else:
        from io import BytesIO
        from pypdf import PdfReader, PdfWriter
        w = PdfWriter()
        for p in pdfs:
            for pg in PdfReader(BytesIO(p)).pages:
                w.add_page(pg)
        buf = BytesIO(); w.write(buf); out = buf.getvalue()
    resp = HttpResponse(out, content_type="application/pdf")
    resp["Content-Disposition"] = 'inline; filename="labels.pdf"'
    return resp


@login_required
@require_POST
def delete_all_labels(request):
    """Delete every shipment that already has a label/item at DHL.

    Tries to cancel each at DHL first (works for prepared/OPEN; finalized
    items can't be deleted via the API), then removes the local records so the
    list is cleared. Drafts (no label yet) are left untouched.
    """
    labelled = list(Shipment.objects.exclude(dpi_item_id=""))
    if not labelled:
        messages.info(request, "No created labels to delete.")
        return redirect("dashboard")
    cancelled = locked = 0
    for s in labelled:
        res = dpi.delete_item(s.dpi_item_id)
        if res.get("ok"):
            cancelled += 1
        elif res.get("finalized"):
            locked += 1
    n = len(labelled)
    Shipment.objects.filter(pk__in=[s.pk for s in labelled]).delete()
    msg = f"🗑️ Изтрити {n} пратка(и) с етикет от списъка."
    if cancelled:
        msg += f" {cancelled} отказани в DHL."
    if locked:
        msg += (
            f" {locked} бяха вече ФИНАЛИЗИРАНИ — не могат да се изтрият в DHL "
            f"(просто не ги изпращай; таксуваш се само при предаване)."
        )
    messages.success(request, msg)
    return redirect("dashboard")


@login_required
@require_POST
def print_paperwork(request):
    """DHL step 3 — the AWB dispatch paperwork for the selected shipments' AWB(s)."""
    sel = _selected(request)
    awbs = []
    for s in sel:
        if s.awb and s.awb not in awbs:
            awbs.append(s.awb)
    if not awbs:
        messages.error(request, "The selected shipments have no AWB yet — finalize first.")
        return redirect("dashboard")
    pdfs = [p for p in (dpi.get_awb_paperwork(a) for a in awbs) if p]
    if not pdfs:
        messages.error(request, "Could not fetch the paperwork — see the logs.")
        return redirect("dashboard")
    if len(pdfs) == 1:
        out = pdfs[0]
    else:
        from io import BytesIO
        from pypdf import PdfReader, PdfWriter
        w = PdfWriter()
        for p in pdfs:
            for pg in PdfReader(BytesIO(p)).pages:
                w.add_page(pg)
        buf = BytesIO(); w.write(buf); out = buf.getvalue()
    resp = HttpResponse(out, content_type="application/pdf")
    resp["Content-Disposition"] = 'inline; filename="awb-paperwork.pdf"'
    return resp


@login_required
@require_POST
def cancel(request):
    """Cancel selected shipments by deleting their DPI item (OPEN only)."""
    sel = [s for s in _selected(request) if s.dpi_item_id]
    if not sel:
        messages.error(request, "Select shipments that are at DHL to cancel.")
        return redirect("dashboard")
    cancelled = locked = failed = 0
    for s in sel:
        res = dpi.delete_item(s.dpi_item_id)
        if res.get("ok"):
            s.status = "cancelled"; s.tracking_number = ""; s.awb = ""; s.dpi_item_id = ""
            s.notes = "Cancelled at DHL."
            s.save(); cancelled += 1
        elif res.get("finalized"):
            s.status = "cancelled"
            s.notes = f"⚠️ Already FINALIZED at DHL — can't delete (AWB {s.awb}). Just don't ship it."
            s.save(update_fields=["status", "notes"]); locked += 1
        else:
            s.notes = f"Cancel failed: {res.get('error')}"
            s.save(update_fields=["notes"]); failed += 1
    if cancelled:
        messages.success(request, f"🗑️ Cancelled {cancelled} shipment(s) at DHL.")
    if locked:
        messages.warning(request, f"⚠️ {locked} were finalized — marked cancelled locally; just don't ship them.")
    if failed:
        messages.error(request, f"{failed} could not be cancelled — see the notes.")
    return redirect("dashboard")
