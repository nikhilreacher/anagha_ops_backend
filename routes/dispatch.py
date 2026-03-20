
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from database import SessionLocal
from models import Shop, Dispatch, Route, Ledger, ReturnTask
from sms_service import send_credit_added_sms

router = APIRouter()

def db():
    d=SessionLocal()
    try: yield d
    finally: d.close()


def parse_datetime_as_naive_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def resolve_beat_value(database, beat: str):
    if beat.startswith("beat") and beat[4:].isdigit():
        route = database.query(Route).filter(Route.id == int(beat[4:])).first()
        if route:
            return route.name
    return beat


def get_dispatch_beats(database, dispatch):
    raw_beats = [beat.strip() for beat in (dispatch.beat or "").split(",") if beat.strip()]
    return [resolve_beat_value(database, beat) for beat in raw_beats]


def serialize_dispatch(dispatch):
    return {
        "id": dispatch.id,
        "beat": dispatch.beat,
        "total_bills": dispatch.total_bills,
        "total_cases": dispatch.total_cases,
        "star_bags_boxes": dispatch.star_bags_boxes,
        "status": dispatch.status,
        "returns_checked": bool(dispatch.returns_checked),
        "new_credits_checked": bool(dispatch.new_credits_checked),
        "new_credit_total": dispatch.new_credit_total or 0,
        "close_notes": dispatch.close_notes,
        "created_at": dispatch.created_at.isoformat(),
        "closed_at": dispatch.closed_at.isoformat() if dispatch.closed_at else None,
    }


def get_dispatch_or_404(database, dispatch_id: int):
    dispatch = database.query(Dispatch).filter(Dispatch.id == dispatch_id).first()
    if not dispatch:
        raise HTTPException(status_code=404, detail="Dispatch not found")
    return dispatch

@router.post("/")
def create_dispatch(
    beat: str,
    total_bills: int,
    total_cases: int,
    star_bags_boxes: int,
    database=Depends(db),
):
    beats = [item.strip() for item in beat.split(",") if item.strip()]
    if not beats:
        raise HTTPException(status_code=400, detail="At least one beat is required")

    resolved_beats = [resolve_beat_value(database, item) for item in beats]
    for beat_value in resolved_beats:
        shops_exist = database.query(Shop).filter(Shop.beat == beat_value).first()
        if not shops_exist:
            raise HTTPException(status_code=404, detail=f"Beat not found: {beat_value}")

    dispatch = Dispatch(
        beat=",".join(beats),
        total_bills=total_bills,
        total_cases=total_cases,
        star_bags_boxes=star_bags_boxes,
    )
    database.add(dispatch)
    database.commit()
    database.refresh(dispatch)

    return {
        "status": "ok",
        "dispatch": serialize_dispatch(dispatch),
    }


@router.get("/")
def list_dispatches(database=Depends(db)):
    dispatches = database.query(Dispatch).order_by(Dispatch.created_at.desc()).all()
    return [serialize_dispatch(dispatch) for dispatch in dispatches]


@router.post("/{dispatch_id}/ledger")
def add_dispatch_credit(
    dispatch_id: int,
    shop_id: int,
    bill_no: str,
    bill_date: str,
    salesman: str = "",
    bill_amt: float = 0,
    paid_amt: float = 0,
    balance: float = 0,
    paid_date: str = "",
    remarks: str = "",
    database=Depends(db),
):
    dispatch = get_dispatch_or_404(database, dispatch_id)
    if dispatch.status != "active":
        raise HTTPException(status_code=400, detail="Dispatch is closed")

    shop = database.query(Shop).filter(Shop.id == shop_id).first()
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")

    beat_value = resolve_beat_value(database, dispatch.beat)
    dispatch_beats = get_dispatch_beats(database, dispatch)
    if shop.beat not in dispatch_beats:
        raise HTTPException(status_code=400, detail="Shop does not belong to this dispatch route")

    if database.query(Ledger).filter(Ledger.bill_no == bill_no).first():
        raise HTTPException(status_code=400, detail="Bill number already exists")

    parsed_bill_date = parse_datetime_as_naive_utc(bill_date)
    parsed_paid_date = parse_datetime_as_naive_utc(paid_date) if paid_date else None

    ledger = Ledger(
        bill_no=bill_no,
        dispatch_id=dispatch.id,
        shop_id=shop.id,
        party=shop.name,
        bill_date=parsed_bill_date,
        delivery_date=dispatch.created_at,
        beat_name=shop.beat,
        salesman=salesman or None,
        bill_amt=bill_amt,
        paid_amt=paid_amt,
        balance=balance,
        paid_date=parsed_paid_date,
        remarks=remarks or None,
    )
    database.add(ledger)
    dispatch.new_credit_total = (dispatch.new_credit_total or 0) + (balance or 0)
    database.commit()

    sms_result = send_credit_added_sms(
        shop_name=shop.name,
        phone=shop.phone,
        bill_no=bill_no,
        balance=balance or 0,
    )

    return {
        "status": "ok",
        "new_credit_total": dispatch.new_credit_total,
        "sms": sms_result,
    }


@router.post("/{dispatch_id}/close")
def close_dispatch(
    dispatch_id: int,
    returns_checked: bool,
    new_credits_checked: bool,
    close_notes: str = "",
    database=Depends(db),
):
    dispatch = get_dispatch_or_404(database, dispatch_id)
    if dispatch.status == "closed":
        raise HTTPException(status_code=400, detail="Dispatch already closed")

    dispatch.status = "closed"
    dispatch.returns_checked = 1 if returns_checked else 0
    dispatch.new_credits_checked = 1 if new_credits_checked else 0
    dispatch.close_notes = close_notes or None
    dispatch.closed_at = datetime.utcnow()
    if returns_checked:
        existing_task = (
            database.query(ReturnTask)
            .filter(ReturnTask.dispatch_id == dispatch.id)
            .filter(ReturnTask.status == "pending")
            .first()
        )
        if not existing_task:
            database.add(
                ReturnTask(
                    dispatch_id=dispatch.id,
                    beat=dispatch.beat,
                    route_label=dispatch.beat,
                )
            )

    database.commit()
    database.refresh(dispatch)

    return {"status": "ok", "dispatch": serialize_dispatch(dispatch)}


@router.get("/{dispatch_id}/shops")
def dispatch_shops(dispatch_id: int, database=Depends(db)):
    dispatch = get_dispatch_or_404(database, dispatch_id)
    if dispatch.status != "active":
        raise HTTPException(status_code=400, detail="Dispatch is closed")

    dispatch_beats = get_dispatch_beats(database, dispatch)
    shops = database.query(Shop).filter(Shop.beat.in_(dispatch_beats)).all()
    shop_ids = [shop.id for shop in shops]
    ledger_rows = (
        database.query(Ledger)
        .filter(Ledger.shop_id.in_(shop_ids) if shop_ids else False)
        .filter(Ledger.balance.isnot(None))
        .filter(Ledger.balance > 0)
        .order_by(Ledger.shop_id.asc(), Ledger.bill_date.asc(), Ledger.bill_no.asc())
        .all()
    )

    bills_by_shop_id = {}
    totals_by_shop_id = {}
    for row in ledger_rows:
        balance = row.balance or 0
        bills_by_shop_id.setdefault(row.shop_id, []).append(
            {
                "bill_no": row.bill_no,
                "bill_date": str(row.bill_date.date()) if row.bill_date else None,
                "delivery_date": str(row.delivery_date.date()) if row.delivery_date else None,
                "balance": balance,
                "remarks": row.remarks,
            }
        )
        totals_by_shop_id[row.shop_id] = (totals_by_shop_id.get(row.shop_id, 0) or 0) + balance

    res = []
    for shop in shops:
        bills = bills_by_shop_id.get(shop.id, [])
        total = totals_by_shop_id.get(shop.id, 0) or 0

        res.append(
            {
                "shop_id": shop.id,
                "shop": shop.name,
                "phone": shop.phone,
                "address": shop.address,
                "lat": shop.lat,
                "lon": shop.lon,
                "outstanding": total,
                "bills": bills,
            }
        )

    res.sort(key=lambda item: item["outstanding"], reverse=True)
    return res
