import argparse
import math
from datetime import datetime

import pandas as pd

from database import SessionLocal
from models import Ledger, Shop

VALID_BUSINESS_TYPES = {"mainline", "icd"}
SHOP_CODE_COLUMNS = ["S.NO", "S. NO", "Sr No", "Sr. No", "Shop ID", "Shop Code"]
SHOP_NAME_COLUMNS = ["Shop", "Shop Name", "Party", "Customer Name", "Name"]
SHOP_PHONE_COLUMNS = ["Phone", "Phone No", "Mobile", "Mobile No", "Contact"]
SHOP_ADDRESS_COLUMNS = ["Address", "Shop Address", "Location"]
SHOP_BEAT_COLUMNS = ["Beat", "Beat Name", "Route", "Route Name"]
SHOP_LAT_COLUMNS = ["Lat", "Latitude"]
SHOP_LON_COLUMNS = ["Lon", "Longitude", "Long"]

LEDGER_COLUMN_MAP = {
    "party": ["Party", "Party Name", "Shop", "Shop Name", "Customer Name"],
    "beat_name": ["Beat Name", "Beat", "Route", "Route Name"],
    "bill_no": ["Bill No", "Bill Number", "Invoice No", "Invoice Number"],
    "bill_date": ["Bill Date", "Invoice Date"],
    "delivery_date": ["Delivery Date"],
    "salesman": ["Salesman", "Salesman Name"],
    "bill_amt": ["Bill Amt", "Bill Amount", "Invoice Amount"],
    "paid_amt": ["Paid Amt", "Paid Amount", "Received Amount"],
    "balance": ["Balance", "Outstanding", "Due Amount"],
    "paid_date": ["Paid Date", "Receipt Date"],
    "remarks": ["Remarks", "Remark", "Notes"],
}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Import shops and/or ledger entries for a selected business type."
    )
    parser.add_argument("--file", required=True, help="Path to the Excel file")
    parser.add_argument(
        "--business-type",
        default="mainline",
        choices=sorted(VALID_BUSINESS_TYPES),
        help="Business bucket for the imported records",
    )
    parser.add_argument(
        "--mode",
        default="both",
        choices=["shops", "ledger", "both"],
        help="What to import from the workbook",
    )
    parser.add_argument(
        "--shops-sheet",
        default="Shops",
        help="Sheet name for the shop master import",
    )
    parser.add_argument(
        "--ledger-sheet",
        default="Sheet1",
        help="Sheet name for the ledger import",
    )
    parser.add_argument(
        "--create-missing-shops",
        action="store_true",
        help="Create missing shops while importing ledger rows",
    )
    return parser.parse_args()


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
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def to_text(value):
    if pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def normalize_column_name(value):
    return str(value).strip().lower().replace(".", "").replace("_", " ")


def first_present(row, column_names):
    normalized_index_map = {
        normalize_column_name(column_name): column_name
        for column_name in row.index
    }
    for column_name in column_names:
        actual_column_name = normalized_index_map.get(normalize_column_name(column_name))
        if actual_column_name is not None:
            value = row.get(actual_column_name)
            if value is not None and not pd.isna(value) and str(value).strip() != "":
                return value
    return None


def find_shop(database, party, beat_name, business_type, external_shop_code=None):
    query = database.query(Shop).filter(Shop.business_type == business_type)

    if external_shop_code:
        shop = query.filter(Shop.external_shop_code == external_shop_code).first()
        if shop:
            return shop

    if party and beat_name:
        shop = query.filter(Shop.name == party, Shop.beat == beat_name).first()
        if shop:
            return shop

    if party:
        shop = query.filter(Shop.name == party).first()
        if shop:
            return shop

    return None


def build_fallback_bill_no(row, row_number, business_type):
    party = to_text(first_present(row, LEDGER_COLUMN_MAP["party"])) or "unknown-shop"
    normalized_party = "".join(ch for ch in party.lower() if ch.isalnum())[:18] or "unknownshop"
    bill_date = to_datetime(first_present(row, LEDGER_COLUMN_MAP["bill_date"]))
    bill_date_token = bill_date.date().isoformat() if bill_date else "old"
    return f"NA-{business_type.upper()}-{normalized_party}-{bill_date_token}-{row_number}"


def normalize_bill_no(raw_bill_no, business_type):
    text_value = to_text(raw_bill_no)
    if not text_value:
        return None
    if business_type == "mainline":
        return text_value

    business_prefix = f"{business_type.upper()}-"
    if text_value.upper().startswith(business_prefix):
        return text_value
    return f"{business_prefix}{text_value}"


def ensure_unique_bill_no(database, base_bill_no, seen_bill_nos, business_type):
    candidate = base_bill_no
    existing = database.query(Ledger).filter(Ledger.bill_no == candidate).first()
    if candidate not in seen_bill_nos and (
        existing is None or candidate.startswith(f"{business_type.upper()}-") or candidate.startswith(f"NA-{business_type.upper()}-")
    ):
        seen_bill_nos.add(candidate)
        return candidate

    suffix = 2
    while candidate in seen_bill_nos or database.query(Ledger).filter(Ledger.bill_no == candidate).first():
        candidate = f"{base_bill_no}-{suffix}"
        suffix += 1
    seen_bill_nos.add(candidate)
    return candidate


def import_shops(database, file_path, sheet_name, business_type):
    df_shops = pd.read_excel(file_path, sheet_name=sheet_name)
    created = 0
    updated = 0

    for _, row in df_shops.iterrows():
        external_shop_code = to_text(first_present(row, SHOP_CODE_COLUMNS))
        shop_name = to_text(first_present(row, SHOP_NAME_COLUMNS))
        if not shop_name:
            continue

        beat_name = to_text(first_present(row, SHOP_BEAT_COLUMNS))
        phone = to_text(first_present(row, SHOP_PHONE_COLUMNS))
        address = to_text(first_present(row, SHOP_ADDRESS_COLUMNS))
        lat = to_text(first_present(row, SHOP_LAT_COLUMNS))
        lon = to_text(first_present(row, SHOP_LON_COLUMNS))
        row_business_type = to_text(first_present(row, ["business_type", "Business Type"]))
        normalized_business_type = (row_business_type or business_type or "mainline").strip().lower()
        if normalized_business_type not in VALID_BUSINESS_TYPES:
            normalized_business_type = business_type

        existing_shop = find_shop(
            database,
            shop_name,
            beat_name,
            normalized_business_type,
            external_shop_code=external_shop_code,
        )
        if existing_shop:
            existing_shop.external_shop_code = external_shop_code
            existing_shop.phone = phone
            existing_shop.address = address
            existing_shop.beat = beat_name
            existing_shop.lat = lat
            existing_shop.lon = lon
            existing_shop.business_type = normalized_business_type
            updated += 1
            continue

        database.add(
            Shop(
                name=shop_name,
                phone=phone,
                address=address,
                beat=beat_name,
                lat=lat,
                lon=lon,
                is_temporary=0,
                external_shop_code=external_shop_code,
                business_type=normalized_business_type,
            )
        )
        created += 1

    database.commit()
    return {"created": created, "updated": updated}


def import_ledger(database, file_path, sheet_name, business_type, create_missing_shops):
    df_ledger = pd.read_excel(file_path, sheet_name=sheet_name)
    inserted = 0
    replaced = 0
    created_shops = 0
    seen_bill_nos = set()

    for row_number, (_, row) in enumerate(df_ledger.iterrows(), start=1):
        party = to_text(first_present(row, LEDGER_COLUMN_MAP["party"]))
        beat_name = to_text(first_present(row, LEDGER_COLUMN_MAP["beat_name"]))
        bill_no = normalize_bill_no(first_present(row, LEDGER_COLUMN_MAP["bill_no"]), business_type)

        if not party:
            continue
        if not bill_no:
            bill_no = build_fallback_bill_no(row, row_number, business_type)
        bill_no = ensure_unique_bill_no(database, bill_no, seen_bill_nos, business_type)

        shop = find_shop(database, party, beat_name, business_type)
        if not shop and create_missing_shops:
            shop = Shop(
                name=party,
                beat=beat_name,
                is_temporary=0,
                business_type=business_type,
            )
            database.add(shop)
            database.flush()
            created_shops += 1

        existing = database.query(Ledger).filter(Ledger.bill_no == bill_no).first()
        if existing:
            database.delete(existing)
            database.flush()
            replaced += 1

        database.add(
            Ledger(
                bill_no=bill_no,
                shop_id=shop.id if shop else None,
                party=party,
                bill_date=to_datetime(first_present(row, LEDGER_COLUMN_MAP["bill_date"])),
                delivery_date=to_datetime(first_present(row, LEDGER_COLUMN_MAP["delivery_date"])),
                beat_name=beat_name,
                salesman=to_text(first_present(row, LEDGER_COLUMN_MAP["salesman"])),
                bill_amt=to_float(first_present(row, LEDGER_COLUMN_MAP["bill_amt"])),
                paid_amt=to_float(first_present(row, LEDGER_COLUMN_MAP["paid_amt"])),
                balance=to_float(first_present(row, LEDGER_COLUMN_MAP["balance"])),
                paid_date=to_datetime(first_present(row, LEDGER_COLUMN_MAP["paid_date"])),
                remarks=to_text(first_present(row, LEDGER_COLUMN_MAP["remarks"])),
                business_type=business_type,
            )
        )
        inserted += 1

    database.commit()
    return {"inserted": inserted, "replaced": replaced, "created_shops": created_shops}


def main():
    args = parse_args()
    database = SessionLocal()

    try:
        if args.business_type not in VALID_BUSINESS_TYPES:
            raise ValueError(f"Unsupported business type: {args.business_type}")

        if args.mode in {"shops", "both"}:
            shop_result = import_shops(
                database=database,
                file_path=args.file,
                sheet_name=args.shops_sheet,
                business_type=args.business_type,
            )
            print(
                f"Shop import complete for {args.business_type}: "
                f"{shop_result['created']} created, {shop_result['updated']} updated"
            )

        if args.mode in {"ledger", "both"}:
            ledger_result = import_ledger(
                database=database,
                file_path=args.file,
                sheet_name=args.ledger_sheet,
                business_type=args.business_type,
                create_missing_shops=args.create_missing_shops,
            )
            print(
                f"Ledger import complete for {args.business_type}: "
                f"{ledger_result['inserted']} inserted, "
                f"{ledger_result['replaced']} replaced, "
                f"{ledger_result['created_shops']} shops created from ledger"
            )
    finally:
        database.close()


if __name__ == "__main__":
    main()
