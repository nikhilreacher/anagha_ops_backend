
from fastapi import APIRouter, Depends, HTTPException
from database import SessionLocal
from models import Shop, ReturnTask, Dispatch, StockEntry, MOCEntry, Expense
from datetime import datetime, timedelta, timezone

router = APIRouter()
IST = timezone(timedelta(hours=5, minutes=30))

def db():
    d=SessionLocal()
    try: yield d
    finally: d.close()


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
    rows = database.query(StockEntry).order_by(StockEntry.stock_date.desc()).all()
    return [
        {
            "id": row.id,
            "stock_date": row.stock_date.isoformat(),
            "stock_count": row.stock_count,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@router.post("/stock")
def add_stock_entry(stock_date: str, stock_count: float, database=Depends(db)):
    parsed_stock_date = datetime.fromisoformat(stock_date)

    existing = (
        database.query(StockEntry)
        .filter(StockEntry.stock_date == parsed_stock_date)
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
        profit = margin - total_expenses - (row.total_discount or 0)

        history.append(
            {
                "id": row.id,
                "moc_month": month_start.date().isoformat(),
                "target_month": month_start.strftime("%B %Y"),
                "total_sales": row.total_sales,
                "total_discount": row.total_discount,
                "closing_stock_value": row.closing_stock_value or 0,
                "total_expenses": total_expenses,
                "margin": margin,
                "profit": profit,
                "created_at": row.created_at.isoformat(),
            }
        )

    return history


@router.post("/moc")
def save_moc(
    total_sales: float,
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
        total_discount=total_discount,
        closing_stock_value=closing_stock_value,
        created_at=datetime.utcnow(),
    )
    database.add(row)
    database.commit()
    return {"status": "ok", "mode": "created"}
