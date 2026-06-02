from django.contrib import admin, messages
from django.http import HttpResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from . import dpi
from .models import Shipment


@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "status_badge",
        "print_link",
        "recipient_name",
        "city",
        "country",
        "weight_g",
        "tracking_number",
        "awb",
        "created_at",
    )
    list_filter = ("status", "country", "created_at")
    search_fields = ("recipient_name", "tracking_number", "awb", "reference", "city")
    readonly_fields = (
        "dpi_order_id",
        "dpi_item_id",
        "awb",
        "tracking_number",
        "label_created_at",
        "created_at",
        "notes",
    )
    actions = ["create_labels", "print_all_awb_labels"]

    fieldsets = (
        (None, {"fields": ("reference", "status", "owner")}),
        ("Recipient", {"fields": (
            "recipient_name", "recipient_phone", "recipient_email",
            "address_line1", "address_line2", "city", "postal_code", "country",
        )}),
        ("Parcel", {"fields": (
            "description", "weight_g", "value", "currency", "product", "service_level",
        )}),
        ("DHL result", {"fields": (
            "tracking_number", "awb", "dpi_item_id", "dpi_order_id",
            "label_created_at", "notes",
        )}),
    )

    # -------- display helpers --------
    @admin.display(description="Status")
    def status_badge(self, obj):
        colors = {
            "draft": "#64748b", "prepared": "#f59e0b", "label_created": "#0ea5e9",
            "shipped": "#16a34a", "cancelled": "#78716c", "failed": "#dc2626",
        }
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;border-radius:4px;'
            'font-size:11px;font-weight:700;">{}</span>',
            colors.get(obj.status, "#64748b"), obj.get_status_display(),
        )

    @admin.display(description="🖨️ Label")
    def print_link(self, obj):
        if not obj.dpi_item_id:
            return format_html('<span style="color:#94a3b8;font-size:11px;">—</span>')
        url = reverse("admin:core_shipment_print_label", args=[obj.pk])
        return format_html(
            '<a href="{}" target="_blank" style="background:#16a34a;color:#fff;'
            'padding:3px 8px;border-radius:4px;font-size:11px;">🖨️ Print</a>', url,
        )

    # -------- actions --------
    @admin.action(description="🖨️ Create label via DHL (finalize)")
    def create_labels(self, request, queryset):
        made = failed = 0
        for s in queryset:
            if s.dpi_item_id:
                continue  # already has a label
            res = dpi.create_label(s, finalize=True)
            if res.get("ok"):
                s.dpi_order_id = str(res.get("order_id") or "")
                s.dpi_item_id = str(res.get("item_id") or "")
                s.awb = str(res.get("awb") or "")
                s.tracking_number = str(res.get("barcode") or res.get("awb") or "")
                s.status = "label_created"
                s.label_created_at = timezone.now()
                s.notes = ""
                s.save()
                made += 1
            else:
                s.status = "failed"
                s.notes = f"HTTP {res.get('status_code')}: {res.get('error')}"
                s.save(update_fields=["status", "notes"])
                failed += 1
        if made:
            messages.success(request, f"🖨️ Created {made} label(s). Click 🖨️ Print on each row.")
        if failed:
            messages.error(request, f"{failed} failed — see each shipment's notes.")

    @admin.action(description="🖨️ Print ALL labels for selected AWB(s) — one PDF")
    def print_all_awb_labels(self, request, queryset):
        awbs = []
        for a in queryset.exclude(awb="").values_list("awb", flat=True):
            if a and a not in awbs:
                awbs.append(a)
        if not awbs:
            self.message_user(request, "No AWBs on the selection yet.", level=messages.WARNING)
            return
        pdfs = [p for p in (dpi.get_item_labels_for_awb(a) for a in awbs) if p]
        if not pdfs:
            self.message_user(request, "Couldn't fetch labels — see logs.", level=messages.ERROR)
            return
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

    # -------- custom URL: stream one label --------
    def get_urls(self):
        urls = super().get_urls()
        return [
            path(
                "<int:pk>/print-label/",
                self.admin_site.admin_view(self.print_label_view),
                name="core_shipment_print_label",
            ),
        ] + urls

    def print_label_view(self, request, pk):
        from django.shortcuts import get_object_or_404

        s = get_object_or_404(Shipment, pk=pk)
        if not s.dpi_item_id:
            return HttpResponse("No label yet — run 'Create label' first.", status=400)
        pdf = dpi.get_item_label(s.dpi_item_id)
        if not pdf:
            return HttpResponse("Label fetch failed — see logs.", status=502)
        resp = HttpResponse(pdf, content_type="application/pdf")
        resp["Content-Disposition"] = f'inline; filename="label-{s.pk}.pdf"'
        return resp
