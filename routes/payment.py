import math

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

from database import SessionLocal
from models import Ledger, PaymentFollowUp, PaymentRequest, Shop

router = APIRouter()
VALID_BUSINESS_TYPES = {"mainline", "icd"}
VALID_ALLOCATION_MODES = {"oldest", "latest_bill", "selected_bill"}


def db():
    d = SessionLocal()
    try:
        yield d
    finally:
        d.close()


def normalize_business_type(value: str | None) -> str:
    normalized = (value or "mainline").strip().lower()
    if normalized not in VALID_BUSINESS_TYPES:
        raise HTTPException(status_code=400, detail="Invalid business type")
    return normalized


def require_finite_number(value: float, detail: str) -> float:
    if not math.isfinite(value):
        raise HTTPException(status_code=400, detail=detail)
    return value


def normalize_allocation_mode(value: str | None) -> str:
    normalized = (value or "oldest").strip().lower()
    if normalized not in VALID_ALLOCATION_MODES:
        raise HTTPException(status_code=400, detail="Invalid allocation mode")
    return normalized


def get_pending_ledger_rows(database, shop_id: int, business_type: str):
    ledger_rows = (
        database.query(Ledger)
        .filter(Ledger.shop_id == shop_id)
        .filter(Ledger.business_type == business_type)
        .filter(Ledger.balance.isnot(None))
        .filter(Ledger.balance > 0)
        .all()
    )
    return ledger_rows


def sort_ledger_rows(ledger_rows, latest_first: bool = False):
    ledger_rows.sort(
        key=lambda row: (
            row.bill_date is None,
            row.bill_date or datetime.max,
            row.bill_no or "",
        ),
        reverse=latest_first,
    )
    return ledger_rows


def build_payment_allocations(ledger_rows, amount: float):
    remaining_amount = amount
    allocations = []
    payment_time = datetime.utcnow()

    for row in ledger_rows:
        if remaining_amount <= 0:
            break

        due = row.balance or 0
        if due <= 0:
            continue

        applied = min(due, remaining_amount)
        row.paid_amt = (row.paid_amt or 0) + applied
        row.balance = due - applied
        row.paid_date = payment_time
        remaining_amount -= applied
        allocations.append(
            {
                "bill_no": row.bill_no,
                "applied_amount": applied,
                "remaining_balance": row.balance,
            }
        )

    return payment_time, allocations, remaining_amount


def apply_payment_to_ledger(database, shop, amount: float, business_type: str, allocation_mode: str = "oldest", bill_no: str | None = None):
    ledger_rows = get_pending_ledger_rows(database, shop.id, business_type)

    if not ledger_rows:
        raise HTTPException(status_code=400, detail="No pending credit found for this shop")

    normalized_mode = normalize_allocation_mode(allocation_mode)
    candidate_rows = ledger_rows
    if normalized_mode == "selected_bill":
        target_bill_no = (bill_no or "").strip()
        if not target_bill_no:
            raise HTTPException(status_code=400, detail="Bill selection is required")
        candidate_rows = [row for row in ledger_rows if (row.bill_no or "") == target_bill_no]
        if not candidate_rows:
            raise HTTPException(status_code=400, detail="Selected bill not found or already settled")
    elif normalized_mode == "latest_bill":
        candidate_rows = sort_ledger_rows(ledger_rows, latest_first=True)[:1]
    else:
        candidate_rows = sort_ledger_rows(ledger_rows)

    payment_time, allocations, remaining_amount = build_payment_allocations(candidate_rows, amount)
    applied_amount = amount - remaining_amount
    remaining_outstanding = sum((row.balance or 0) for row in ledger_rows if (row.balance or 0) > 0)
    return {
        "allocations": allocations,
        "applied_amount": applied_amount,
        "payment_time": payment_time,
        "remaining_outstanding": remaining_outstanding,
        "unapplied_amount": remaining_amount,
    }


def serialize_payment_request(row, shop):
    return {
        "id": row.id,
        "shop_id": row.shop_id,
        "shop": shop.name if shop else "Shop",
        "beat": shop.beat if shop else None,
        "amount": row.amount,
        "status": row.status,
        "requested_by": row.requested_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "received_at": row.received_at.isoformat() if row.received_at else None,
        "received_by": row.received_by,
        "bill_no": row.bill_no,
        "allocation_mode": row.allocation_mode or "oldest",
        "business_type": row.business_type,
    }


def serialize_payment_history_event(paid_at, rows):
    sorted_rows = sorted(
        rows,
        key=lambda row: (
            row.bill_date is None,
            row.bill_date or datetime.max,
            row.bill_no or "",
        )
    )
    return {
        "paid_at": paid_at.isoformat() if paid_at else None,
        "amount": sum((row.paid_amt or 0) for row in sorted_rows),
        "bills": [
            {
                "bill_no": row.bill_no,
                "bill_date": row.bill_date.isoformat() if row.bill_date else None,
                "applied_amount": row.paid_amt or 0,
                "remaining_balance": row.balance or 0,
            }
            for row in sorted_rows
        ],
    }


def parse_followup_date(value: str | None) -> datetime:
    raw = (value or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="Follow-up date is required")
    try:
        return datetime.strptime(raw, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Follow-up date must be in YYYY-MM-DD format") from exc


def serialize_payment_followup(row, shop):
    return {
        "id": row.id,
        "shop_id": row.shop_id,
        "shop": shop.name if shop else "Shop",
        "beat": shop.beat if shop else None,
        "followup_date": row.followup_date.date().isoformat() if row.followup_date else None,
        "note": row.note,
        "status": row.status,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "completed_by": row.completed_by,
        "business_type": row.business_type,
    }


def complete_active_followups(database, shop_id: int, business_type: str, completed_by: str):
    active_rows = (
        database.query(PaymentFollowUp)
        .filter(PaymentFollowUp.shop_id == shop_id)
        .filter(PaymentFollowUp.business_type == business_type)
        .filter(PaymentFollowUp.status == "pending")
        .all()
    )
    if not active_rows:
        return

    completed_at = datetime.utcnow()
    for row in active_rows:
        row.status = "completed"
        row.completed_at = completed_at
        row.completed_by = completed_by
        row.updated_at = completed_at


@router.get("/shops")
def shops_with_pending_credit(beat: str = "", business_type: str = "mainline", database=Depends(db)):
    normalized_business_type = normalize_business_type(business_type)
    query = database.query(Shop).filter(Shop.business_type == normalized_business_type)
    if beat:
        query = query.filter(Shop.beat == beat)

    shops = query.order_by(Shop.name).all()
    result = []

    for shop in shops:
        outstanding_rows = (
            database.query(Ledger)
            .filter(Ledger.shop_id == shop.id)
            .filter(Ledger.business_type == normalized_business_type)
            .filter(Ledger.balance.isnot(None))
            .filter(Ledger.balance > 0)
            .all()
        )
        total_due = sum((row.balance or 0) for row in outstanding_rows)
        if total_due <= 0:
            continue

        result.append(
            {
                "shop_id": shop.id,
                "shop": shop.name,
                "beat": shop.beat,
                "outstanding": total_due,
            }
        )

    return result


@router.get("/requests")
def list_payment_requests(
    business_type: str = "mainline",
    status: str = "",
    database=Depends(db),
):
    normalized_business_type = normalize_business_type(business_type)
    query = database.query(PaymentRequest).filter(PaymentRequest.business_type == normalized_business_type)
    if status:
        query = query.filter(PaymentRequest.status == status)

    rows = query.order_by(PaymentRequest.created_at.desc(), PaymentRequest.id.desc()).all()
    shop_map = {
        shop.id: shop
        for shop in database.query(Shop).filter(Shop.business_type == normalized_business_type).all()
    }
    return [serialize_payment_request(row, shop_map.get(row.shop_id)) for row in rows]


@router.get("/history/{shop_id}")
def shop_payment_history(
    shop_id: int,
    business_type: str = "mainline",
    limit: int = 2,
    database=Depends(db),
):
    normalized_business_type = normalize_business_type(business_type)
    shop = (
        database.query(Shop)
        .filter(Shop.id == shop_id)
        .filter(Shop.business_type == normalized_business_type)
        .first()
    )
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")

    safe_limit = max(1, min(limit, 10))
    ledger_rows = (
        database.query(Ledger)
        .filter(Ledger.shop_id == shop.id)
        .filter(Ledger.business_type == normalized_business_type)
        .filter(Ledger.paid_date.isnot(None))
        .filter(Ledger.paid_amt.isnot(None))
        .filter(Ledger.paid_amt > 0)
        .order_by(Ledger.paid_date.desc(), Ledger.bill_no.asc())
        .all()
    )

    grouped_order = []
    rows_by_paid_at = {}
    for row in ledger_rows:
        paid_at = row.paid_date
        if paid_at not in rows_by_paid_at:
            rows_by_paid_at[paid_at] = []
            grouped_order.append(paid_at)
        rows_by_paid_at[paid_at].append(row)

    history = [
        serialize_payment_history_event(paid_at, rows_by_paid_at[paid_at])
        for paid_at in grouped_order[:safe_limit]
    ]
    return {
        "shop_id": shop.id,
        "shop": shop.name,
        "history": history,
    }


@router.get("/followups")
def list_payment_followups(
    business_type: str = "mainline",
    scope: str = "all",
    shop_id: int | None = None,
    database=Depends(db),
):
    normalized_business_type = normalize_business_type(business_type)
    normalized_scope = (scope or "all").strip().lower()
    if normalized_scope not in {"all", "today"}:
        raise HTTPException(status_code=400, detail="Invalid follow-up scope")

    query = (
        database.query(PaymentFollowUp)
        .filter(PaymentFollowUp.business_type == normalized_business_type)
        .filter(PaymentFollowUp.status == "pending")
    )
    if shop_id is not None:
        query = query.filter(PaymentFollowUp.shop_id == shop_id)
    if normalized_scope == "today":
        today_value = datetime.utcnow().date().isoformat()
        query = query.filter(PaymentFollowUp.followup_date >= f"{today_value} 00:00:00")
        query = query.filter(PaymentFollowUp.followup_date <= f"{today_value} 23:59:59")

    rows = query.order_by(PaymentFollowUp.followup_date.asc(), PaymentFollowUp.updated_at.desc()).all()
    shop_ids = {row.shop_id for row in rows}
    shop_map = {
        shop.id: shop
        for shop in database.query(Shop)
        .filter(Shop.business_type == normalized_business_type)
        .filter(Shop.id.in_(shop_ids) if shop_ids else False)
        .all()
    }
    return [serialize_payment_followup(row, shop_map.get(row.shop_id)) for row in rows]


@router.post("/followups")
def create_or_update_payment_followup(
    shop_id: int,
    followup_date: str,
    created_by: str,
    note: str = "",
    business_type: str = "mainline",
    database=Depends(db),
):
    normalized_business_type = normalize_business_type(business_type)
    if not (created_by or "").strip():
        raise HTTPException(status_code=400, detail="Creator is required")

    shop = (
        database.query(Shop)
        .filter(Shop.id == shop_id)
        .filter(Shop.business_type == normalized_business_type)
        .first()
    )
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")

    parsed_followup_date = parse_followup_date(followup_date)
    now = datetime.utcnow()
    row = (
        database.query(PaymentFollowUp)
        .filter(PaymentFollowUp.shop_id == shop.id)
        .filter(PaymentFollowUp.business_type == normalized_business_type)
        .filter(PaymentFollowUp.status == "pending")
        .order_by(PaymentFollowUp.updated_at.desc(), PaymentFollowUp.id.desc())
        .first()
    )

    if row:
        row.followup_date = parsed_followup_date
        row.note = note.strip() or None
        row.updated_at = now
        row.created_by = created_by.strip()
    else:
        row = PaymentFollowUp(
            shop_id=shop.id,
            followup_date=parsed_followup_date,
            note=note.strip() or None,
            created_by=created_by.strip(),
            created_at=now,
            updated_at=now,
            business_type=normalized_business_type,
        )
        database.add(row)

    database.commit()
    database.refresh(row)
    return {"status": "ok", "followup": serialize_payment_followup(row, shop)}


@router.post("/requests")
def create_payment_request(
    shop_id: int,
    amount: float,
    requested_by: str,
    bill_no: str = "",
    allocation_mode: str = "oldest",
    business_type: str = "mainline",
    database=Depends(db),
):
    normalized_business_type = normalize_business_type(business_type)
    require_finite_number(amount, "Amount must be a valid number")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than zero")
    if not (requested_by or "").strip():
        raise HTTPException(status_code=400, detail="Requester is required")
    normalized_allocation_mode = normalize_allocation_mode(allocation_mode)
    normalized_bill_no = (bill_no or "").strip()
    if normalized_allocation_mode == "selected_bill" and not normalized_bill_no:
        raise HTTPException(status_code=400, detail="Please select a bill")

    shop = (
        database.query(Shop)
        .filter(Shop.id == shop_id)
        .filter(Shop.business_type == normalized_business_type)
        .first()
    )
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")

    row = PaymentRequest(
        shop_id=shop.id,
        amount=amount,
        requested_by=requested_by.strip(),
        bill_no=normalized_bill_no or None,
        allocation_mode=normalized_allocation_mode,
        business_type=normalized_business_type,
    )
    database.add(row)
    complete_active_followups(database, shop.id, normalized_business_type, requested_by.strip())
    database.commit()
    database.refresh(row)
    return {"status": "ok", "request": serialize_payment_request(row, shop)}


@router.post("/requests/{request_id}/receive")
def mark_payment_request_received(
    request_id: int,
    received_by: str,
    business_type: str = "mainline",
    database=Depends(db),
):
    normalized_business_type = normalize_business_type(business_type)
    if not (received_by or "").strip():
        raise HTTPException(status_code=400, detail="Receiver is required")

    row = (
        database.query(PaymentRequest)
        .filter(PaymentRequest.id == request_id)
        .filter(PaymentRequest.business_type == normalized_business_type)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Payment request not found")
    if row.status != "pending":
        raise HTTPException(status_code=400, detail="Payment request already processed")

    shop = (
        database.query(Shop)
        .filter(Shop.id == row.shop_id)
        .filter(Shop.business_type == normalized_business_type)
        .first()
    )
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")

    payment_result = apply_payment_to_ledger(
        database,
        shop,
        row.amount,
        normalized_business_type,
        allocation_mode=row.allocation_mode or "oldest",
        bill_no=row.bill_no,
    )
    row.status = "received"
    row.received_at = payment_result["payment_time"]
    row.received_by = received_by.strip()
    complete_active_followups(database, shop.id, normalized_business_type, received_by.strip())
    database.commit()

    return {
        "status": "ok",
        "request": serialize_payment_request(row, shop),
        "shop": shop.name,
        "applied_amount": payment_result["applied_amount"],
        "unapplied_amount": payment_result["unapplied_amount"],
        "allocations": payment_result["allocations"],
    }


@router.post("/")
def collect_payment(
    shop_id: int,
    amount: float,
    bill_no: str = "",
    allocation_mode: str = "oldest",
    business_type: str = "mainline",
    database=Depends(db),
):
    normalized_business_type = normalize_business_type(business_type)
    require_finite_number(amount, "Amount must be a valid number")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than zero")
    normalized_allocation_mode = normalize_allocation_mode(allocation_mode)
    normalized_bill_no = (bill_no or "").strip()
    if normalized_allocation_mode == "selected_bill" and not normalized_bill_no:
        raise HTTPException(status_code=400, detail="Please select a bill")

    shop = (
        database.query(Shop)
        .filter(Shop.id == shop_id)
        .filter(Shop.business_type == normalized_business_type)
        .first()
    )
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")

    payment_result = apply_payment_to_ledger(
        database,
        shop,
        amount,
        normalized_business_type,
        allocation_mode=normalized_allocation_mode,
        bill_no=normalized_bill_no,
    )
    complete_active_followups(database, shop.id, normalized_business_type, "Admin")
    database.commit()

    return {
        "status": "ok",
        "shop_id": shop.id,
        "shop": shop.name,
        "requested_amount": amount,
        "applied_amount": payment_result["applied_amount"],
        "unapplied_amount": payment_result["unapplied_amount"],
        "allocations": payment_result["allocations"],
    }
