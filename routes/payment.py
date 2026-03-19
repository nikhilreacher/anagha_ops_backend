from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

from database import SessionLocal
from models import Ledger, Shop

router = APIRouter()


def db():
    d = SessionLocal()
    try:
        yield d
    finally:
        d.close()


@router.get("/shops")
def shops_with_pending_credit(beat: str = "", database=Depends(db)):
    query = database.query(Shop)
    if beat:
        query = query.filter(Shop.beat == beat)

    shops = query.order_by(Shop.name).all()
    result = []

    for shop in shops:
        outstanding_rows = (
            database.query(Ledger)
            .filter(Ledger.shop_id == shop.id)
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


@router.post("/")
def collect_payment(shop_id: int, amount: float, database=Depends(db)):
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than zero")

    shop = database.query(Shop).filter(Shop.id == shop_id).first()
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")

    ledger_rows = (
        database.query(Ledger)
        .filter(Ledger.shop_id == shop_id)
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

    database.commit()

    return {
        "status": "ok",
        "shop_id": shop.id,
        "shop": shop.name,
        "requested_amount": amount,
        "applied_amount": amount - remaining_amount,
        "unapplied_amount": remaining_amount,
        "allocations": allocations,
    }
