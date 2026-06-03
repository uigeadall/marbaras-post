from django.conf import settings
from django.db import models


class Shipment(models.Model):
    """One parcel to be labelled and dispatched via DHL / DPI Global Mail."""

    STATUS_CHOICES = [
        ("draft", "Чернова"),
        ("prepared", "Подготвя се в DHL"),
        ("label_created", "Етикет създаден"),
        ("shipped", "Изпратена"),
        ("cancelled", "Отказана"),
        ("failed", "Неуспешна"),
    ]

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shipments",
    )
    reference = models.CharField(
        max_length=40, blank=True, help_text="Your own order reference (optional)"
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="draft", db_index=True
    )

    # Recipient
    recipient_name = models.CharField(max_length=80)
    recipient_phone = models.CharField(max_length=40, blank=True)
    recipient_email = models.EmailField(blank=True)
    address_line1 = models.CharField(max_length=120)
    address_line2 = models.CharField(max_length=120, blank=True)
    address_line3 = models.CharField(max_length=120, blank=True)
    city = models.CharField(max_length=40)
    state = models.CharField(max_length=40, blank=True, help_text="State/region (optional)")
    postal_code = models.CharField(max_length=16)
    country = models.CharField(max_length=2, help_text="ISO 3166-1 alpha-2, e.g. DE")

    # Parcel
    description = models.CharField(max_length=64, default="Goods")
    quantity = models.PositiveIntegerField(default=1, help_text="Number of items in the parcel")
    weight_g = models.PositiveIntegerField(default=100, help_text="Gross weight in grams (parcel incl. packaging)")
    net_weight_g = models.PositiveIntegerField(
        default=0, help_text="Net weight of the goods only (customs); 0 = use parcel weight"
    )
    # Multiple customs content lines: [{description, quantity, net_weight, value}].
    # Empty → fall back to the single description/quantity/value/net_weight_g.
    contents_json = models.JSONField(default=list, blank=True)
    value = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    currency = models.CharField(max_length=3, default="EUR")
    product = models.CharField(
        max_length=8, blank=True, help_text="DPI product; blank = auto (GPT)"
    )
    service_level = models.CharField(max_length=16, default="PRIORITY")

    # Customs (required by DHL for non-EU destinations)
    CONTENT_TYPE_CHOICES = [
        ("SALE_GOODS", "Продажба на стоки"),
        ("GIFT", "Подарък"),
        ("COMMERCIAL_SAMPLE", "Търговска мостра"),
        ("RETURN_GOODS", "Върнати стоки"),
        ("DOCUMENTS", "Документи"),
        ("OTHERS", "Друго"),
    ]
    content_type = models.CharField(
        max_length=20, choices=CONTENT_TYPE_CHOICES, default="SALE_GOODS"
    )
    hs_code = models.CharField(
        max_length=20, default="711311", help_text="HS tariff number (customs)"
    )
    origin_country = models.CharField(
        max_length=2, default="BG", help_text="Country of origin (customs)"
    )
    # Sender's customs/tax reference — e.g. IOSS / VOEC number. DPI senderTaxId.
    tax_id = models.CharField(
        max_length=35, blank=True,
        help_text="Sender VAT / IOSS, e.g. 'IOSS: IM1234567890'",
    )
    # Importer (recipient) customs reference — e.g. Etsy UK VAT or an EORI
    # number. Prints as "Importer tax/customs ref" on the CN22. DPI importerTaxId.
    importer_tax_id = models.CharField(
        max_length=35, blank=True,
        help_text="Importer customs ref, e.g. 'ETSY UK VAT:GB365883274' or 'EORI: ...'",
    )

    # DHL results
    dpi_order_id = models.CharField(max_length=40, blank=True)
    dpi_item_id = models.CharField(max_length=40, blank=True)
    awb = models.CharField(max_length=40, blank=True, db_index=True)
    tracking_number = models.CharField(max_length=40, blank=True, db_index=True)
    label_created_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"#{self.pk} {self.recipient_name} → {self.country} ({self.get_status_display()})"

    @property
    def tracking_url(self):
        if not self.tracking_number:
            return ""
        return f"https://www.dhl.com/global-en/home/tracking.html?tracking-id={self.tracking_number}"
