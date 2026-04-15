
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from database import SessionLocal
from models import Shop, ReturnTask, Dispatch, StockEntry, MOCEntry, Expense, Ledger
from datetime import datetime, timedelta, timezone

router = APIRouter()
IST = timezone(timedelta(hours=5, minutes=30))

def db():
    d=SessionLocal()
    try: yield d
    finally: d.close()


def normalize_icd_bill_no(database, bill_no: str | None, shop_id: int):
    value = (bill_no or "").strip()
    if not value:
        value = f"NA-ICD-IT-{shop_id}-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"
    elif not value.upper().startswith("ICD-") and not value.upper().startswith("NA-ICD-"):
        value = f"ICD-{value}"

    candidate = value
    suffix = 2
    while True:
        existing = database.query(Ledger).filter(Ledger.bill_no == candidate).first()
        if not existing:
            return candidate
        candidate = f"{value}-{suffix}"
        suffix += 1


def month_bounds(reference_date: datetime):
    month_start = reference_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_month = (month_start + timedelta(days=32)).replace(day=1)
    return month_start, next_month


def previous_month_bounds(reference_date: datetime):
    current_month_start, _ = month_bounds(reference_date)
    previous_month_last_day = current_month_start - timedelta(days=1)
    previous_month_start = previous_month_last_day.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return previous_month_start, current_month_start


def current_moc_target_month(reference_date: datetime):
    current_month_start, _ = month_bounds(reference_date)
    previous_month_start, _ = previous_month_bounds(reference_date)
    return current_month_start if reference_date.day >= 20 else previous_month_start


def now_ist():
    return datetime.now(IST).replace(tzinfo=None)


def sum_expenses_in_range(database, start_date: datetime, end_date: datetime):
    rows = (
        database.query(Expense)
        .filter(Expense.expense_date >= start_date)
        .filter(Expense.expense_date < end_date)
        .all()
    )
    return sum((row.amount or 0) for row in rows)


@router.get("/icd-credit/shops")
def get_icd_credit_shops(beat: str = "", search: str = "", database=Depends(db)):
    query = database.query(Shop).filter(Shop.business_type == "icd")
    if beat:
        query = query.filter(Shop.beat == beat)
    if search:
        query = query.filter(Shop.name.ilike(f"%{search.strip()}%"))

    shops = query.order_by(Shop.name.asc()).all()
    return [
        {
            "shop_id": shop.id,
            "shop": shop.name,
            "beat": shop.beat,
            "phone": shop.phone,
            "address": shop.address,
        }
        for shop in shops
    ]


@router.post("/icd-credit")
def add_icd_credit(
    shop_id: int,
    bill_amt: float,
    paid_amt: float = 0,
    bill_no: str = "",
    bill_date: str = "",
    delivery_date: str = "",
    remarks: str = "",
    created_by: str = "",
    database=Depends(db),
):
    if bill_amt <= 0:
        raise HTTPException(status_code=400, detail="Bill amount must be greater than zero")
    if paid_amt < 0:
        raise HTTPException(status_code=400, detail="Paid amount cannot be negative")
    if paid_amt > bill_amt:
        raise HTTPException(status_code=400, detail="Paid amount cannot exceed bill amount")

    shop = (
        database.query(Shop)
        .filter(Shop.id == shop_id)
        .filter(Shop.business_type == "icd")
        .first()
    )
    if not shop:
        raise HTTPException(status_code=404, detail="ICD shop not found")

    final_bill_no = normalize_icd_bill_no(database, bill_no, shop.id)
    parsed_bill_date = datetime.strptime(bill_date, "%Y-%m-%d") if bill_date else datetime.utcnow()
    parsed_delivery_date = datetime.strptime(delivery_date, "%Y-%m-%d") if delivery_date else parsed_bill_date
    final_remarks = remarks.strip() if remarks else ""
    if created_by.strip():
        final_remarks = f"{final_remarks} | Added by {created_by.strip()}".strip(" |")

    row = Ledger(
        bill_no=final_bill_no,
        dispatch_id=None,
        shop_id=shop.id,
        party=shop.name,
        bill_date=parsed_bill_date,
        delivery_date=parsed_delivery_date,
        beat_name=shop.beat,
        salesman=None,
        bill_amt=bill_amt,
        paid_amt=paid_amt,
        balance=bill_amt - paid_amt,
        paid_date=parsed_delivery_date if paid_amt > 0 else None,
        remarks=final_remarks or None,
        business_type="icd",
    )
    database.add(row)
    database.commit()
    database.refresh(row)

    return {
        "status": "ok",
        "ledger": {
            "bill_no": row.bill_no,
            "shop_id": row.shop_id,
            "shop": shop.name,
            "beat": shop.beat,
            "bill_amt": row.bill_amt,
            "paid_amt": row.paid_amt,
            "balance": row.balance,
        },
    }


@router.get("/icd-credit/recent")
def get_recent_icd_credit_entries(limit: int = 10, database=Depends(db)):
    safe_limit = min(max(limit, 1), 50)
    rows = (
        database.query(Ledger, Shop)
        .outerjoin(Shop, Shop.id == Ledger.shop_id)
        .filter(Ledger.business_type == "icd")
        .order_by(Ledger.bill_date.desc(), Ledger.delivery_date.desc(), Ledger.bill_no.desc())
        .limit(safe_limit)
        .all()
    )

    return [
        {
            "bill_no": ledger.bill_no,
            "shop_id": ledger.shop_id,
            "shop": shop.name if shop else ledger.party,
            "beat": ledger.beat_name or (shop.beat if shop else None),
            "bill_date": ledger.bill_date.date().isoformat() if ledger.bill_date else None,
            "delivery_date": ledger.delivery_date.date().isoformat() if ledger.delivery_date else None,
            "bill_amt": ledger.bill_amt or 0,
            "paid_amt": ledger.paid_amt or 0,
            "balance": ledger.balance or 0,
            "remarks": ledger.remarks,
        }
        for ledger, shop in rows
    ]

@router.get("/")
def get_shops(database=Depends(db)):
    return database.query(Shop).all()


@router.get("/beats")
def get_beats(database=Depends(db)):
    beats = (
        database.query(Shop.beat)
        .filter(Shop.beat.isnot(None))
        .filter(Shop.beat != "")
        .distinct()
        .all()
    )
    return [{"id": beat, "name": beat.replace("beat", "Beat ")} for (beat,) in beats]


@router.get("/returns")
def get_returns(database=Depends(db)):
    tasks = database.query(ReturnTask).order_by(ReturnTask.created_at.desc()).all()
    dispatch_map = {
        dispatch.id: dispatch for dispatch in database.query(Dispatch).all()
    }
    grouped = {}
    for task in tasks:
        if task.dispatch_id not in grouped:
            grouped[task.dispatch_id] = []
        grouped[task.dispatch_id].append(task)

    result = []
    for dispatch_id, rows in grouped.items():
        primary = rows[0]
        statuses = [row.status for row in rows]
        if any(status == "pending" for status in statuses):
            status = "pending"
        elif any(status == "completed" for status in statuses):
            status = "completed"
        else:
            status = "discarded"

        resolved_candidates = [row.resolved_at for row in rows if row.resolved_at]
        resolved_at = max(resolved_candidates).isoformat() if resolved_candidates else None
        route_label = primary.route_label or ", ".join(
            sorted({row.beat for row in rows if row.beat})
        )

        result.append(
            {
                "id": primary.id,
                "dispatch_id": dispatch_id,
                "task_type": getattr(primary, "task_type", "return"),
                "beat": primary.beat,
                "route_label": route_label,
                "created_at": primary.created_at.isoformat(),
                "status": status,
                "resolved_at": resolved_at,
                "dispatch_created_at": (
                    dispatch_map[dispatch_id].created_at.isoformat()
                    if dispatch_id in dispatch_map
                    else None
                ),
            }
        )

    return result


@router.post("/returns/{task_id}")
def update_return(task_id: int, action: str, database=Depends(db)):
    task = database.query(ReturnTask).filter(ReturnTask.id == task_id).first()
    if not task:
        return {"status": "not_found"}

    if action not in {"completed", "discarded"}:
        return {"status": "invalid_action"}

    task.status = action
    task.resolved_at = datetime.utcnow()
    database.commit()
    return {"status": "ok"}


@router.get("/stock")
def get_stock_entries(database=Depends(db)):
    rows = (
        database.query(StockEntry)
        .order_by(StockEntry.stock_date.desc(), StockEntry.created_at.desc(), StockEntry.id.desc())
        .all()
    )

    unique_rows = []
    seen_dates = set()
    for row in rows:
      day_key = row.stock_date.date().isoformat()
      if day_key in seen_dates:
          continue
      seen_dates.add(day_key)
      unique_rows.append(row)

    return [
        {
            "id": row.id,
            "stock_date": row.stock_date.isoformat(),
            "stock_count": row.stock_count,
            "created_at": row.created_at.isoformat(),
        }
        for row in unique_rows
    ]


@router.post("/stock")
def add_stock_entry(stock_date: str, stock_count: float, database=Depends(db)):
    parsed_stock_date = datetime.fromisoformat(stock_date)

    existing = (
        database.query(StockEntry)
        .filter(func.date(StockEntry.stock_date) == parsed_stock_date.date().isoformat())
        .order_by(StockEntry.created_at.desc(), StockEntry.id.desc())
        .first()
    )
    if existing:
        existing.stock_count = stock_count
        existing.created_at = datetime.utcnow()
        database.commit()
        return {"status": "ok", "mode": "updated"}

    row = StockEntry(
        stock_date=parsed_stock_date,
        stock_count=stock_count,
        created_at=datetime.utcnow(),
    )
    database.add(row)
    database.commit()
    return {"status": "ok", "mode": "created"}


@router.get("/moc")
def get_moc_status(database=Depends(db)):
    now = now_ist()
    target_month_start = current_moc_target_month(now)
    existing = (
        database.query(MOCEntry)
        .filter(MOCEntry.moc_month == target_month_start)
        .order_by(MOCEntry.id.desc())
        .first()
    )
    return {
        "allowed": 20 <= now.day <= 22,
        "target_month": target_month_start.strftime("%B %Y"),
        "moc_month": target_month_start.date().isoformat(),
        "entry": (
            {
                "id": existing.id,
                "total_sales": existing.total_sales,
                "total_icd_sales": existing.total_icd_sales or 0,
                "total_discount": existing.total_discount,
                "closing_stock_value": existing.closing_stock_value or 0,
            }
            if existing
            else None
        ),
    }


@router.get("/moc/history")
def get_moc_history(database=Depends(db)):
    rows = database.query(MOCEntry).order_by(MOCEntry.moc_month.desc(), MOCEntry.id.desc()).all()
    history = []
    for row in rows:
        month_start = row.moc_month.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        _, month_end = month_bounds(month_start)
        total_expenses = sum_expenses_in_range(database, month_start, month_end)
        margin = (row.total_sales or 0) * 0.039
        icd_profit = (row.total_icd_sales or 0) * 0.14
        profit = margin - total_expenses - (row.total_discount or 0)

        history.append(
            {
                "id": row.id,
                "moc_month": month_start.date().isoformat(),
                "target_month": month_start.strftime("%B %Y"),
                "total_sales": row.total_sales,
                "total_icd_sales": row.total_icd_sales or 0,
                "total_discount": row.total_discount,
                "closing_stock_value": row.closing_stock_value or 0,
                "total_expenses": total_expenses,
                "margin": margin,
                "icd_profit": icd_profit,
                "profit": profit,
                "created_at": row.created_at.isoformat(),
            }
        )

    return history


@router.post("/moc")
def save_moc(
    total_sales: float,
    total_icd_sales: float = 0,
    total_discount: float = 0,
    closing_stock_value: float = 0,
    moc_month: str = "",
    database=Depends(db),
):
    now = now_ist()
    if moc_month:
        parsed = datetime.strptime(moc_month, "%Y-%m")
        target_month_start = parsed.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        target_month_start = current_moc_target_month(now)

    existing = (
        database.query(MOCEntry)
        .filter(MOCEntry.moc_month == target_month_start)
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="MOC entry already added for this period")

    row = MOCEntry(
        moc_month=target_month_start,
        total_sales=total_sales,
        total_icd_sales=total_icd_sales,
        total_discount=total_discount,
        closing_stock_value=closing_stock_value,
        created_at=datetime.utcnow(),
    )
    database.add(row)
    database.commit()
    return {"status": "ok", "mode": "created"}
