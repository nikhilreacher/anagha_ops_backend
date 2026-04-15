from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

from database import SessionLocal
from models import Ledger, PaymentRequest, Shop
from sms_service import send_payment_received_sms

router = APIRouter()
VALID_BUSINESS_TYPES = {"mainline", "icd"}


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


def apply_payment_to_ledger(database, shop, amount: float, business_type: str):
    ledger_rows = (
        database.query(Ledger)
        .filter(Ledger.shop_id == shop.id)
        .filter(Ledger.business_type == business_type)
        .filter(Ledger.balance.isnot(None))
        .filter(Ledger.balance > 0)
        .all()
    )
    ledger_rows.sort(
        key=lambda row: (
            row.bill_date is None,
            row.bill_date or datetime.max,
            row.bill_no or "",
        )
    )

    if not ledger_rows:
        raise HTTPException(status_code=400, detail="No pending credit found for this shop")

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
        "business_type": row.business_type,
    }


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


@router.post("/requests")
def create_payment_request(
    shop_id: int,
    amount: float,
    requested_by: str,
    business_type: str = "mainline",
    database=Depends(db),
):
    normalized_business_type = normalize_business_type(business_type)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than zero")
    if not (requested_by or "").strip():
        raise HTTPException(status_code=400, detail="Requester is required")

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
        business_type=normalized_business_type,
    )
    database.add(row)
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

    payment_result = apply_payment_to_ledger(database, shop, row.amount, normalized_business_type)
    row.status = "received"
    row.received_at = payment_result["payment_time"]
    row.received_by = received_by.strip()
    database.commit()

    sms_result = send_payment_received_sms(
        shop_name=shop.name,
        phone=shop.phone,
        applied_amount=payment_result["applied_amount"],
        remaining_amount=payment_result["remaining_outstanding"],
    )

    return {
        "status": "ok",
        "request": serialize_payment_request(row, shop),
        "shop": shop.name,
        "applied_amount": payment_result["applied_amount"],
        "unapplied_amount": payment_result["unapplied_amount"],
        "allocations": payment_result["allocations"],
        "sms": sms_result,
    }


@router.post("/")
def collect_payment(shop_id: int, amount: float, business_type: str = "mainline", database=Depends(db)):
    normalized_business_type = normalize_business_type(business_type)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than zero")

    shop = (
        database.query(Shop)
        .filter(Shop.id == shop_id)
        .filter(Shop.business_type == normalized_business_type)
        .first()
    )
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")

    payment_result = apply_payment_to_ledger(database, shop, amount, normalized_business_type)
    database.commit()
    sms_result = send_payment_received_sms(
        shop_name=shop.name,
        phone=shop.phone,
        applied_amount=payment_result["applied_amount"],
        remaining_amount=payment_result["remaining_outstanding"],
    )

    return {
        "status": "ok",
        "shop_id": shop.id,
        "shop": shop.name,
        "requested_amount": amount,
        "applied_amount": payment_result["applied_amount"],
        "unapplied_amount": payment_result["unapplied_amount"],
        "allocations": payment_result["allocations"],
        "sms": sms_result,
    }
