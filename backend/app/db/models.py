"""All tables (PROMPT.md section 8).

Conventions:
- Money is BIGINT paise (`*_paise`). GST is basis points (`*_bp`).
- Quantities are BIGINT milli-units of the item's canonical unit (`*_milli`).
- Timestamps are timestamptz in UTC. Calendar dates (needed-by, validity) are IST dates.
- Tenant-owned rows carry `builder_org_id`; every query on them must filter by it.
- Mutable workflow rows carry `version` for optimistic locking.
"""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map = {
        dict[str, Any]: JSONB,
        list[Any]: JSONB,
        datetime: DateTime(timezone=True),
    }


class Row(Base):
    __abstract__ = True

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


def org_fk(nullable: bool = False) -> Mapped[Any]:
    return mapped_column(ForeignKey("builder_orgs.id"), index=True, nullable=nullable)


def text_array() -> Mapped[list[str]]:
    return mapped_column(ARRAY(Text), default=list, server_default="{}")


# --- organisations, people, places -------------------------------------------------


class BuilderOrg(Row):
    __tablename__ = "builder_orgs"

    name: Mapped[str] = mapped_column(Text, unique=True)
    gstin: Mapped[str | None] = mapped_column(Text, unique=True)
    settings: Mapped[dict[str, Any]] = mapped_column(default=dict)


class User(Row):
    __tablename__ = "users"

    builder_org_id: Mapped[uuid.UUID | None] = org_fk(nullable=True)  # null = platform admin
    phone: Mapped[str] = mapped_column(Text, unique=True)  # E.164
    name: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)  # owner | purchase_manager | site_engineer | admin
    approval_limit_paise: Mapped[int | None] = mapped_column(BigInteger)  # null = unlimited
    is_active: Mapped[bool] = mapped_column(default=True)


class OtpChallenge(Row):
    __tablename__ = "otp_challenges"

    phone: Mapped[str] = mapped_column(Text, index=True)
    code_hash: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime]
    attempts: Mapped[int] = mapped_column(default=0)
    consumed_at: Mapped[datetime | None]


class Site(Row):
    __tablename__ = "sites"

    builder_org_id: Mapped[uuid.UUID] = org_fk()
    name: Mapped[str] = mapped_column(Text)
    address: Mapped[str] = mapped_column(Text)
    area: Mapped[str] = mapped_column(Text)  # coarse location shown to vendors pre-award
    pincode: Mapped[str] = mapped_column(Text)
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    contact_name: Mapped[str | None] = mapped_column(Text)
    contact_phone: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (UniqueConstraint("builder_org_id", "name"),)


# --- catalog ------------------------------------------------------------------------


class CatalogItem(Row):
    __tablename__ = "catalog_items"

    code: Mapped[str] = mapped_column(Text, unique=True)  # stable key, e.g. OPC53
    category: Mapped[str] = mapped_column(Text, index=True)
    name: Mapped[str] = mapped_column(Text)
    grade: Mapped[str | None] = mapped_column(Text)
    canonical_unit: Mapped[str] = mapped_column(Text)
    aliases: Mapped[list[str]] = text_array()
    hsn: Mapped[str | None] = mapped_column(Text)
    default_gst_bp: Mapped[int] = mapped_column(Integer)

    __table_args__ = (
        Index(
            "ix_catalog_items_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )


class UnitConversion(Row):
    """1 from_unit == numerator/denominator to_unit. item_id null = applies to all items."""

    __tablename__ = "unit_conversions"

    item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("catalog_items.id"))
    from_unit: Mapped[str] = mapped_column(Text)
    to_unit: Mapped[str] = mapped_column(Text)
    numerator: Mapped[int] = mapped_column(BigInteger)
    denominator: Mapped[int] = mapped_column(BigInteger)

    __table_args__ = (
        UniqueConstraint("item_id", "from_unit", "to_unit", postgresql_nulls_not_distinct=True),
    )


# --- vendors --------------------------------------------------------------------------


class Vendor(Row):
    __tablename__ = "vendors"

    legal_name: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    phone: Mapped[str] = mapped_column(Text, unique=True)
    gstin: Mapped[str | None] = mapped_column(Text, unique=True)
    opted_in_at: Mapped[datetime | None]
    opted_out_at: Mapped[datetime | None]
    languages: Mapped[list[str]] = text_array()  # "en", "hi"
    categories: Mapped[list[str]] = text_array()
    item_codes: Mapped[list[str]] = text_array()  # catalog item codes supplied
    brands: Mapped[list[str]] = text_array()
    service_pincodes: Mapped[list[str]] = text_array()
    service_radius_km: Mapped[int] = mapped_column(default=0)
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    capacity_per_week: Mapped[dict[str, Any]] = mapped_column(default=dict)  # code -> milli
    min_order_qty: Mapped[dict[str, Any]] = mapped_column(default=dict)  # code -> milli
    credit_days: Mapped[int] = mapped_column(default=0)
    # Reliability, in basis points (10000 = 100%). Updated when orders close.
    on_time_bp: Mapped[int] = mapped_column(default=8000)
    qty_accuracy_bp: Mapped[int] = mapped_column(default=9000)
    invoice_match_bp: Mapped[int] = mapped_column(default=9000)
    response_bp: Mapped[int] = mapped_column(default=8000)
    quality_bp: Mapped[int] = mapped_column(default=8000)
    orders_completed: Mapped[int] = mapped_column(default=0)
    persona: Mapped[str] = mapped_column(Text, default="cooperative")  # demo only


class BuilderVendorLink(Row):
    __tablename__ = "builder_vendor_links"

    builder_org_id: Mapped[uuid.UUID] = org_fk()
    vendor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vendors.id"), index=True)
    status: Mapped[str] = mapped_column(Text, default="active")  # active | blocked
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (UniqueConstraint("builder_org_id", "vendor_id"),)


# --- BOM and RFQ ----------------------------------------------------------------------


class Bom(Row):
    __tablename__ = "boms"

    builder_org_id: Mapped[uuid.UUID] = org_fk()
    public_code: Mapped[str] = mapped_column(Text, unique=True)
    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sites.id"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(Text, default="draft")
    revision: Mapped[int] = mapped_column(default=1)
    source: Mapped[str] = mapped_column(Text, default="manual")  # upload | manual | voice
    original_file_ref: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(default=1)

    __mapper_args__ = {"version_id_col": version}


class BomLine(Row):
    __tablename__ = "bom_lines"

    builder_org_id: Mapped[uuid.UUID] = org_fk()
    bom_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("boms.id"), index=True)
    line_no: Mapped[int]
    catalog_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("catalog_items.id"))
    raw_text: Mapped[str] = mapped_column(Text)
    qty_canonical_milli: Mapped[int] = mapped_column(BigInteger)
    unit: Mapped[str] = mapped_column(Text)  # unit as entered
    qty_entered: Mapped[str] = mapped_column(Text)  # quantity as entered
    needed_by: Mapped[date] = mapped_column(Date)
    partial_allowed: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(Text, default="open")

    __table_args__ = (UniqueConstraint("bom_id", "line_no"),)


class Rfq(Row):
    __tablename__ = "rfqs"

    builder_org_id: Mapped[uuid.UUID] = org_fk()
    bom_line_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bom_lines.id"), index=True)
    public_code: Mapped[str] = mapped_column(Text, unique=True)
    status: Mapped[str] = mapped_column(Text, default="draft")
    bid_window_opens_at: Mapped[datetime | None]
    bid_window_closes_at: Mapped[datetime | None]
    window_extended: Mapped[bool] = mapped_column(default=False)
    max_rounds: Mapped[int] = mapped_column(default=3)
    shortlist_size: Mapped[int] = mapped_column(default=3)
    # Private to code. Never sent to the LLM or a vendor.
    target_price_paise: Mapped[int | None] = mapped_column(BigInteger)
    max_price_paise: Mapped[int | None] = mapped_column(BigInteger)
    revision: Mapped[int] = mapped_column(default=1)
    stale: Mapped[bool] = mapped_column(default=False)
    version: Mapped[int] = mapped_column(default=1)

    __mapper_args__ = {"version_id_col": version}


class RfqInvitation(Row):
    __tablename__ = "rfq_invitations"

    builder_org_id: Mapped[uuid.UUID] = org_fk()
    rfq_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rfqs.id"), index=True)
    vendor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vendors.id"), index=True)
    match_score: Mapped[int] = mapped_column(default=0)  # 0-100
    match_reasons: Mapped[dict[str, Any]] = mapped_column(default=dict)
    invited_at: Mapped[datetime | None]
    reminded_at: Mapped[datetime | None]
    status: Mapped[str] = mapped_column(Text, default="proposed")

    __table_args__ = (UniqueConstraint("rfq_id", "vendor_id"),)


class Quote(Row):
    __tablename__ = "quotes"

    builder_org_id: Mapped[uuid.UUID] = org_fk()
    public_code: Mapped[str] = mapped_column(Text, unique=True)
    rfq_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rfqs.id"), index=True)
    vendor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vendors.id"), index=True)
    revision: Mapped[int] = mapped_column(default=1)
    source: Mapped[str] = mapped_column(Text)  # form | pdf | photo | text | email_sim
    raw_file_ref: Mapped[str | None] = mapped_column(Text)
    raw_text: Mapped[str | None] = mapped_column(Text)
    unit_price_paise: Mapped[int | None] = mapped_column(BigInteger)
    price_unit: Mapped[str | None] = mapped_column(Text)
    gst_included: Mapped[bool] = mapped_column(default=False)
    gst_bp: Mapped[int | None] = mapped_column(Integer)
    freight_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    freight_included: Mapped[bool] = mapped_column(default=True)
    unloading_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    delivery_date: Mapped[date | None] = mapped_column(Date)
    validity_until: Mapped[date | None] = mapped_column(Date)
    payment_terms_days: Mapped[int | None] = mapped_column(Integer)
    brand: Mapped[str | None] = mapped_column(Text)
    qty_offered_milli: Mapped[int | None] = mapped_column(BigInteger)
    stated_total_paise: Mapped[int | None] = mapped_column(BigInteger)
    parse_confidence: Mapped[int | None] = mapped_column(Integer)  # 0-100
    confirmed_by_vendor_at: Mapped[datetime | None]
    received_at: Mapped[datetime | None]
    status: Mapped[str] = mapped_column(Text, default="draft_parsed")
    flags: Mapped[dict[str, Any]] = mapped_column(default=dict)
    version: Mapped[int] = mapped_column(default=1)

    __table_args__ = (UniqueConstraint("rfq_id", "vendor_id", "revision"),)
    __mapper_args__ = {"version_id_col": version}


class NegotiationThread(Row):
    __tablename__ = "negotiation_threads"

    builder_org_id: Mapped[uuid.UUID] = org_fk()
    public_code: Mapped[str] = mapped_column(Text, unique=True)
    rfq_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rfqs.id"), index=True)
    vendor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vendors.id"), index=True)
    state: Mapped[str] = mapped_column(Text, default="open")
    round: Mapped[int] = mapped_column(default=0)
    opening_offer_paise: Mapped[int | None] = mapped_column(BigInteger)
    current_offer_paise: Mapped[int | None] = mapped_column(BigInteger)
    last_counter_paise: Mapped[int | None] = mapped_column(BigInteger)
    deadline_at: Mapped[datetime | None]
    reply_due_at: Mapped[datetime | None]
    nudged: Mapped[bool] = mapped_column(default=False)
    unclear_count: Mapped[int] = mapped_column(default=0)
    parse_fail_count: Mapped[int] = mapped_column(default=0)
    disclosure_asks: Mapped[int] = mapped_column(default=0)
    handoff_reason: Mapped[str | None] = mapped_column(Text)
    handed_to_human_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    version: Mapped[int] = mapped_column(default=1)

    __table_args__ = (UniqueConstraint("rfq_id", "vendor_id"),)
    __mapper_args__ = {"version_id_col": version}


class Message(Row):
    __tablename__ = "messages"

    builder_org_id: Mapped[uuid.UUID | None] = org_fk(nullable=True)
    vendor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vendors.id"), index=True)
    thread_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("negotiation_threads.id"))
    invitation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("rfq_invitations.id"))
    rfq_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("rfqs.id"), index=True)
    direction: Mapped[str] = mapped_column(Text)  # in | out
    channel: Mapped[str] = mapped_column(Text, default="simulated_whatsapp")
    external_message_id: Mapped[str] = mapped_column(Text, unique=True)
    body: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    sent_at: Mapped[datetime | None]
    delivered_at: Mapped[datetime | None]
    read_at: Mapped[datetime | None]
    template_name: Mapped[str | None] = mapped_column(Text)
    in_24h_window: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(Text, default="sent")  # sent | blocked | failed


# --- evaluation, approval, orders -------------------------------------------------------


class Recommendation(Row):
    __tablename__ = "recommendations"

    builder_org_id: Mapped[uuid.UUID] = org_fk()
    rfq_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rfqs.id"), index=True)
    ranked: Mapped[list[Any]] = mapped_column(default=list)
    l1_vendor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("vendors.id"))
    lowest_price_vendor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("vendors.id"))
    split_proposal: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    generated_at: Mapped[datetime]
    is_current: Mapped[bool] = mapped_column(default=True)


class Approval(Row):
    __tablename__ = "approvals"

    builder_org_id: Mapped[uuid.UUID] = org_fk()
    rfq_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rfqs.id"), index=True)
    approver_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    decision: Mapped[str] = mapped_column(Text)  # approved | review | routed_to_owner
    idempotency_key: Mapped[str] = mapped_column(Text, unique=True)
    note: Mapped[str | None] = mapped_column(Text)


class WorkOrder(Row):
    __tablename__ = "work_orders"

    builder_org_id: Mapped[uuid.UUID] = org_fk()
    public_code: Mapped[str] = mapped_column(Text, unique=True)
    rfq_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rfqs.id"), index=True)
    vendor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vendors.id"), index=True)
    quote_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("quotes.id"))
    qty_milli: Mapped[int] = mapped_column(BigInteger)
    unit_price_paise: Mapped[int] = mapped_column(BigInteger)
    subtotal_paise: Mapped[int] = mapped_column(BigInteger)
    gst_paise: Mapped[int] = mapped_column(BigInteger)
    freight_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    total_paise: Mapped[int] = mapped_column(BigInteger)
    delivery_date: Mapped[date | None] = mapped_column(Date)
    partial_allowed: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(Text, default="issued")
    confirm_by: Mapped[datetime | None]
    confirmed_at: Mapped[datetime | None]
    closed_at: Mapped[datetime | None]
    pdf_ref: Mapped[str | None] = mapped_column(Text)
    shortfall_note: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(default=1)

    __mapper_args__ = {"version_id_col": version}


class CapacityReservation(Row):
    """Cross-tenant on purpose: a vendor's weekly capacity is shared by all builders."""

    __tablename__ = "capacity_reservations"

    vendor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vendors.id"))
    catalog_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("catalog_items.id"))
    week_start: Mapped[date] = mapped_column(Date)  # IST Monday
    qty_reserved_milli: Mapped[int] = mapped_column(BigInteger)
    work_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("work_orders.id"), unique=True)
    released_at: Mapped[datetime | None]

    __table_args__ = (
        Index("ix_capacity_vendor_item_week", "vendor_id", "catalog_item_id", "week_start"),
    )


class Delivery(Row):
    __tablename__ = "deliveries"

    builder_org_id: Mapped[uuid.UUID] = org_fk()
    public_code: Mapped[str] = mapped_column(Text, unique=True)
    work_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("work_orders.id"), index=True)
    seq: Mapped[int]
    dispatched_at: Mapped[datetime | None]
    vehicle_no: Mapped[str | None] = mapped_column(Text)
    dispatched_qty_milli: Mapped[int | None] = mapped_column(BigInteger)
    received_qty_milli: Mapped[int | None] = mapped_column(BigInteger)
    received_photo_ref: Mapped[str | None] = mapped_column(Text)
    received_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    received_at: Mapped[datetime | None]
    status: Mapped[str] = mapped_column(Text, default="dispatched")
    flags: Mapped[dict[str, Any]] = mapped_column(default=dict)

    __table_args__ = (UniqueConstraint("work_order_id", "seq"),)


class Invoice(Row):
    __tablename__ = "invoices"

    builder_org_id: Mapped[uuid.UUID] = org_fk()
    work_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("work_orders.id"), index=True)
    file_ref: Mapped[str | None] = mapped_column(Text)
    invoice_no: Mapped[str | None] = mapped_column(Text)
    po_code_stated: Mapped[str | None] = mapped_column(Text)
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    unit_price_paise: Mapped[int | None] = mapped_column(BigInteger)
    mismatch_flags: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(Text, default="received")  # received | matched | flagged


class PriceHistory(Row):
    __tablename__ = "price_history"

    catalog_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("catalog_items.id"))
    region: Mapped[str] = mapped_column(Text)  # pincode prefix, e.g. "201"
    unit_price_paise: Mapped[int] = mapped_column(BigInteger)
    landed_paise: Mapped[int] = mapped_column(BigInteger)
    price_date: Mapped[date] = mapped_column("date", Date)
    work_order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("work_orders.id"), unique=True
    )

    __table_args__ = (
        Index("ix_price_history_item_region_date", "catalog_item_id", "region", "date"),
    )


# --- infrastructure ------------------------------------------------------------------------


class Job(Row):
    __tablename__ = "jobs"

    kind: Mapped[str] = mapped_column(Text)
    run_at: Mapped[datetime]
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    ordering_key: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="pending")  # pending|running|done|failed
    attempts: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    locked_by: Mapped[str | None] = mapped_column(Text)
    locked_at: Mapped[datetime | None]
    dedupe_key: Mapped[str | None] = mapped_column(Text, unique=True)

    __table_args__ = (Index("ix_jobs_status_run_at", "status", "run_at"),)


class AuditLog(Row):
    """Append-only. A trigger rejects UPDATE and DELETE."""

    __tablename__ = "audit_log"

    builder_org_id: Mapped[uuid.UUID | None] = org_fk(nullable=True)
    actor: Mapped[str] = mapped_column(Text)  # "user:<id>", "vendor:<id>", "system:<agent>"
    action: Mapped[str] = mapped_column(Text)
    entity: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[str | None] = mapped_column(Text)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    at: Mapped[datetime]


class DemoClockState(Base):
    """Single row holding the demo clock offset, shared by API and worker."""

    __tablename__ = "demo_clock"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False, default=1)
    offset_seconds: Mapped[int] = mapped_column(BigInteger, default=0)
