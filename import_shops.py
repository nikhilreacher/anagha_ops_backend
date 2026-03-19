from datetime import datetime

import pandas as pd

from database import SessionLocal
from models import Ledger, Shop


db = SessionLocal()


def to_datetime(value):
    if pd.isna(value) or value == "":
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    if isinstance(parsed, pd.Timestamp):
        return parsed.to_pydatetime()
    if isinstance(parsed, datetime):
        return parsed
    return None


def to_float(value):
    if pd.isna(value) or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_text(value):
    if pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def find_shop_id(party, beat_name):
    query = db.query(Shop)

    if party:
        shop = query.filter(Shop.name == party).first()
        if shop:
            return shop.id

    if party and beat_name:
        shop = query.filter(Shop.name == party, Shop.beat == beat_name).first()
        if shop:
            return shop.id

    return None


df_ledger = pd.read_excel(
    "C:\\Users\\ANAGHA ENTERPRISES\\OneDrive\\Documents\\sample.xlsx",
    sheet_name="Sheet1",
)

for _, row in df_ledger.iterrows():
    party = to_text(row.get("Party"))
    beat_name = to_text(row.get("Beat Name"))
    bill_no = to_text(row.get("Bill No"))

    if not bill_no or not party:
        continue

    existing = db.query(Ledger).filter(Ledger.bill_no == bill_no).first()
    if existing:
        db.delete(existing)
        db.flush()

    ledger = Ledger(
        bill_no=bill_no,
        shop_id=find_shop_id(party, beat_name),
        party=party,
        bill_date=to_datetime(row.get("Bill Date")),
        delivery_date=to_datetime(row.get("Delivery Date")),
        beat_name=beat_name,
        salesman=to_text(row.get("Salesman")),
        bill_amt=to_float(row.get("Bill Amt")),
        paid_amt=to_float(row.get("Paid Amt")),
        balance=to_float(row.get("Balance")),
        paid_date=to_datetime(row.get("Paid Date")),
        remarks=to_text(row.get("Remarks")),
    )
    db.add(ledger)

db.commit()
print("Ledger imported successfully")
