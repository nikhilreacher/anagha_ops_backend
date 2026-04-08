
from fastapi import APIRouter, Depends, HTTPException
from database import SessionLocal
from sqlalchemy import func
from models import (
    Shop,
    Invoice,
    StockEntry,
    Ledger,
    Expense,
    Dispatch,
    Employee,
    EmployeeAdvance,
    SalaryPayment,
    MOCEntry,
)
from datetime import datetime, timedelta, timezone

router = APIRouter()
IST = timezone(timedelta(hours=5, minutes=30))

EXPENSE_TYPES = [
    "Stationary",
    "Fuel",
    "Wifi",
    "Electricity Bill",
    "Misc",
    "Salary",
    "Water",
    "Rent",
]

EMPLOYEE_ROLES = ["Sales", "Picker", "Driver", "Helper", "IT"]

def db():
    d=SessionLocal()
    try: yield d
    finally: d.close()


def coerce_naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def serialize_credit_bill(row):
    bill_date = coerce_naive_utc(row.bill_date)
    delivery_date = coerce_naive_utc(row.delivery_date)
    return {
        "bill_no": row.bill_no,
        "bill_date": bill_date.date().isoformat() if bill_date else None,
        "delivery_date": delivery_date.date().isoformat() if delivery_date else None,
        "balance": row.balance or 0,
        "remarks": row.remarks,
    }


def get_credit_summary(database):
    shops = database.query(Shop).all()
    shop_map = {shop.id: shop for shop in shops}
    ledger_rows = (
        database.query(Ledger)
        .filter(Ledger.shop_id.isnot(None))
        .filter(Ledger.balance.isnot(None))
        .filter(Ledger.balance > 0)
        .order_by(Ledger.shop_id.asc(), Ledger.bill_date.asc(), Ledger.bill_no.asc())
        .all()
    )

    grouped = {}
    now = datetime.utcnow()
    for row in ledger_rows:
        shop = shop_map.get(row.shop_id)
        if not shop:
            continue

        entry = grouped.setdefault(
            row.shop_id,
            {
                "shop_id": shop.id,
                "shop": shop.name,
                "beat": shop.beat,
                "outstanding": 0,
                "max_age": 0,
                "bill_count": 0,
            },
        )

        balance = row.balance or 0
        bill_date = coerce_naive_utc(row.bill_date)
        age = (now - bill_date).days if bill_date else 0
        entry["outstanding"] += balance
        entry["max_age"] = max(entry["max_age"], age)
        entry["bill_count"] += 1

    res = list(grouped.values())
    res.sort(key=lambda x: (x["max_age"], x["outstanding"]), reverse=True)
    return res


@router.get("/credit")
def credit(database=Depends(db)):
    return get_credit_summary(database)


@router.get("/credit/{shop_id}/bills")
def credit_shop_bills(shop_id: int, database=Depends(db)):
    shop = database.query(Shop).filter(Shop.id == shop_id).first()
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")

    ledger_rows = (
        database.query(Ledger)
        .filter(Ledger.shop_id == shop_id)
        .filter(Ledger.balance.isnot(None))
        .filter(Ledger.balance > 0)
        .order_by(Ledger.bill_date.asc(), Ledger.bill_no.asc())
        .all()
    )

    bills = [serialize_credit_bill(row) for row in ledger_rows]
    return {
        "shop_id": shop.id,
        "shop": shop.name,
        "bills": bills,
    }


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


def salary_cycle_month(reference_date: datetime):
    current_month_start, _ = month_bounds(reference_date)
    previous_month_start, _ = previous_month_bounds(reference_date)
    return previous_month_start if 1 <= reference_date.day <= 10 else current_month_start


def working_days_in_month(month_start: datetime, month_end: datetime):
    current = month_start
    working_days = 0
    while current < month_end:
      if current.weekday() != 6:
          working_days += 1
      current += timedelta(days=1)
    return working_days


def total_days_in_month(month_start: datetime, month_end: datetime):
    return (month_end - month_start).days


def sum_expenses_in_range(database, start_date: datetime, end_date: datetime):
    rows = (
        database.query(Expense)
        .filter(Expense.expense_date >= start_date)
        .filter(Expense.expense_date < end_date)
        .order_by(Expense.expense_date.desc(), Expense.id.desc())
        .all()
    )

    total = sum((row.amount or 0) for row in rows)
    by_type = {}
    for row in rows:
        key = row.expense_type or "Misc"
        by_type[key] = by_type.get(key, 0) + (row.amount or 0)

    breakdown = [
        {"type": key, "amount": value}
        for key, value in sorted(by_type.items(), key=lambda item: item[1], reverse=True)
    ]

    return total, breakdown, rows


def sum_employee_advances_in_range(database, start_date: datetime, end_date: datetime):
    total = (
        database.query(func.sum(EmployeeAdvance.amount))
        .filter(EmployeeAdvance.advance_date >= start_date)
        .filter(EmployeeAdvance.advance_date < end_date)
        .scalar()
    )
    return total or 0


def build_employee_advance_maps(database, salary_month_start: datetime, salary_month_end: datetime):
    outstanding_advances = {
        row.employee_id: row.total or 0
        for row in (
            database.query(
                EmployeeAdvance.employee_id.label("employee_id"),
                func.sum(EmployeeAdvance.amount).label("total"),
            )
            .group_by(EmployeeAdvance.employee_id)
            .all()
        )
    }
    total_deductions = {
        row.employee_id: row.total or 0
        for row in (
            database.query(
                SalaryPayment.employee_id.label("employee_id"),
                func.sum(SalaryPayment.advance_deduction).label("total"),
            )
            .group_by(SalaryPayment.employee_id)
            .all()
        )
    }
    salary_month_advances = {
        row.employee_id: row.total or 0
        for row in (
            database.query(
                EmployeeAdvance.employee_id.label("employee_id"),
                func.sum(EmployeeAdvance.amount).label("total"),
            )
            .filter(EmployeeAdvance.advance_date >= salary_month_start)
            .filter(EmployeeAdvance.advance_date < salary_month_end)
            .group_by(EmployeeAdvance.employee_id)
            .all()
        )
    }
    return outstanding_advances, total_deductions, salary_month_advances


def serialize_employee(employee, outstanding_advance=0):
    return {
        "id": employee.id,
        "name": employee.name,
        "role": employee.role,
        "phone": employee.phone,
        "salary": employee.salary,
        "outstanding_advance": outstanding_advance,
        "salary_month_advance": 0,
        "salary_cycle_paid": False,
        "created_at": employee.created_at.isoformat(),
    }


def get_employee_or_404(database, employee_id: int):
    employee = database.query(Employee).filter(Employee.id == employee_id).first()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    return employee


def get_employee_outstanding_advance(database, employee_id: int):
    total_advances = sum(
        (row.amount or 0)
        for row in database.query(EmployeeAdvance).filter(EmployeeAdvance.employee_id == employee_id).all()
    )
    total_deductions = sum(
        (row.advance_deduction or 0)
        for row in database.query(SalaryPayment).filter(SalaryPayment.employee_id == employee_id).all()
    )
    return max(total_advances - total_deductions, 0)


def get_employee_advances_for_range(database, employee_id: int, start_date: datetime, end_date: datetime):
    rows = (
        database.query(EmployeeAdvance)
        .filter(EmployeeAdvance.employee_id == employee_id)
        .filter(EmployeeAdvance.advance_date >= start_date)
        .filter(EmployeeAdvance.advance_date < end_date)
        .all()
    )
    return sum((row.amount or 0) for row in rows)


def employee_salary_paid_for_cycle(database, employee_id: int, salary_month_start: datetime):
    payments = (
        database.query(SalaryPayment)
        .filter(SalaryPayment.employee_id == employee_id)
        .all()
    )
    target_cycle_date = coerce_naive_utc(salary_month_start).date()
    return any(coerce_naive_utc(salary_cycle_month(row.payment_date)).date() == target_cycle_date for row in payments)


def serialize_employee_advance(row):
    return {
        "id": row.id,
        "employee_id": row.employee_id,
        "advance_date": row.advance_date.date().isoformat(),
        "amount": row.amount,
        "note": row.note,
        "created_at": row.created_at.isoformat(),
    }


@router.get("/employees")
def list_employees(database=Depends(db)):
    employees = database.query(Employee).order_by(Employee.name.asc()).all()
    return [serialize_employee(employee, get_employee_outstanding_advance(database, employee.id)) for employee in employees]


@router.get("/employees/{employee_id}")
def employee_detail(employee_id: int, database=Depends(db)):
    employee = get_employee_or_404(database, employee_id)
    outstanding_advance = get_employee_outstanding_advance(database, employee.id)
    now = now_ist()
    salary_month_start = salary_cycle_month(now)
    _, salary_month_end = month_bounds(salary_month_start)
    salary_month_advances = get_employee_advances_for_range(
        database, employee.id, salary_month_start, salary_month_end
    )
    advances = (
        database.query(EmployeeAdvance)
        .filter(EmployeeAdvance.employee_id == employee.id)
        .order_by(EmployeeAdvance.advance_date.desc(), EmployeeAdvance.id.desc())
        .all()
    )
    latest_salary_payment = (
        database.query(SalaryPayment)
        .filter(SalaryPayment.employee_id == employee.id)
        .order_by(SalaryPayment.payment_date.desc(), SalaryPayment.id.desc())
        .first()
    )

    return {
        "employee": {
            **serialize_employee(employee, outstanding_advance),
            "salary_month_advance": salary_month_advances,
            "salary_cycle_paid": employee_salary_paid_for_cycle(database, employee.id, salary_month_start),
        },
        "advances": [serialize_employee_advance(row) for row in advances],
        "salary_summary": {
            "salary_month": salary_month_start.strftime("%B %Y"),
            "monthly_salary": employee.salary or 0,
            "salary_month_advance": salary_month_advances,
            "outstanding_advance": outstanding_advance,
            "latest_salary_paid": latest_salary_payment.paid_amount if latest_salary_payment else 0,
            "latest_salary_payment_date": latest_salary_payment.payment_date.date().isoformat() if latest_salary_payment else None,
        },
    }


@router.post("/employees")
def add_employee(
    name: str,
    role: str,
    phone: str = "",
    salary: float = 0,
    database=Depends(db),
):
    role = (role or "").strip()
    if role not in EMPLOYEE_ROLES:
        raise HTTPException(status_code=400, detail="Invalid employee role")
    if salary <= 0:
        raise HTTPException(status_code=400, detail="Salary must be greater than zero")

    employee = Employee(
        name=(name or "").strip(),
        role=role,
        phone=(phone or "").strip() or None,
        salary=salary,
    )
    if not employee.name:
        raise HTTPException(status_code=400, detail="Employee name is required")

    database.add(employee)
    database.commit()
    database.refresh(employee)
    return {"status": "ok", "employee": serialize_employee(employee, 0)}


@router.post("/employees/{employee_id}")
def update_employee(
    employee_id: int,
    name: str,
    role: str,
    phone: str = "",
    salary: float = 0,
    database=Depends(db),
):
    employee = get_employee_or_404(database, employee_id)
    role = (role or "").strip()
    if role not in EMPLOYEE_ROLES:
        raise HTTPException(status_code=400, detail="Invalid employee role")
    if salary <= 0:
        raise HTTPException(status_code=400, detail="Salary must be greater than zero")
    if not (name or "").strip():
        raise HTTPException(status_code=400, detail="Employee name is required")

    employee.name = name.strip()
    employee.role = role
    employee.phone = (phone or "").strip() or None
    employee.salary = salary
    database.commit()
    database.refresh(employee)
    return {"status": "ok", "employee": serialize_employee(employee, get_employee_outstanding_advance(database, employee.id))}


@router.post("/employees/{employee_id}/delete")
def delete_employee(employee_id: int, database=Depends(db)):
    employee = get_employee_or_404(database, employee_id)
    has_advances = database.query(EmployeeAdvance).filter(EmployeeAdvance.employee_id == employee_id).first()
    has_salary_payments = database.query(SalaryPayment).filter(SalaryPayment.employee_id == employee_id).first()
    if has_advances or has_salary_payments:
        raise HTTPException(status_code=400, detail="Cannot delete employee with advance or salary history")

    database.delete(employee)
    database.commit()
    return {"status": "ok"}


@router.post("/employees/{employee_id}/advances")
def add_employee_advance(
    employee_id: int,
    advance_date: str,
    amount: float,
    note: str = "",
    database=Depends(db),
):
    employee = get_employee_or_404(database, employee_id)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Advance amount must be greater than zero")

    row = EmployeeAdvance(
        employee_id=employee.id,
        advance_date=datetime.strptime(advance_date, "%Y-%m-%d"),
        amount=amount,
        note=(note or "").strip() or None,
    )
    database.add(row)
    database.commit()
    database.refresh(row)

    return {
        "status": "ok",
        "advance": {
            **serialize_employee_advance(row),
            "employee_name": employee.name,
        },
    }


@router.post("/employees/{employee_id}/advances/{advance_id}")
def update_employee_advance(
    employee_id: int,
    advance_id: int,
    advance_date: str,
    amount: float,
    note: str = "",
    database=Depends(db),
):
    employee = get_employee_or_404(database, employee_id)
    row = (
        database.query(EmployeeAdvance)
        .filter(EmployeeAdvance.id == advance_id)
        .filter(EmployeeAdvance.employee_id == employee.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Advance entry not found")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Advance amount must be greater than zero")

    row.advance_date = datetime.strptime(advance_date, "%Y-%m-%d")
    row.amount = amount
    row.note = (note or "").strip() or None
    database.commit()
    database.refresh(row)

    return {
        "status": "ok",
        "advance": {
            **serialize_employee_advance(row),
            "employee_name": employee.name,
        },
    }


@router.post("/salary/pay")
def pay_salary(
    employee_id: int,
    payment_date: str,
    absent_days: float,
    database=Depends(db),
):
    employee = get_employee_or_404(database, employee_id)
    if absent_days < 0:
        raise HTTPException(status_code=400, detail="Days cannot be negative")

    parsed_payment_date = datetime.strptime(payment_date, "%Y-%m-%d")
    if not (1 <= parsed_payment_date.day <= 10):
        raise HTTPException(status_code=400, detail="Salary payment is allowed only from 1st to 10th")

    salary_month_start = salary_cycle_month(parsed_payment_date)
    _, salary_month_end = month_bounds(salary_month_start)
    if employee_salary_paid_for_cycle(database, employee.id, salary_month_start):
        raise HTTPException(status_code=400, detail=f"Salary already paid for {salary_month_start.strftime('%B %Y')}")
    working_days = working_days_in_month(salary_month_start, salary_month_end)
    total_days = total_days_in_month(salary_month_start, salary_month_end)
    if absent_days > working_days:
        raise HTTPException(status_code=400, detail="Present and absent days exceed salary month's working days")
    present_days = working_days - absent_days

    payable_absent_days = max(absent_days - 1, 0)
    daily_salary = (employee.salary or 0) / total_days if employee.salary and total_days else 0
    absent_deduction = daily_salary * payable_absent_days
    available_after_absence = max((employee.salary or 0) - absent_deduction, 0)
    salary_month_advances = get_employee_advances_for_range(
        database, employee.id, salary_month_start, salary_month_end
    )
    advance_deduction = min(salary_month_advances, available_after_absence)
    paid_amount = max(available_after_absence - advance_deduction, 0)

    salary_payment = SalaryPayment(
        employee_id=employee.id,
        payment_date=parsed_payment_date,
        present_days=present_days,
        absent_days=absent_days,
        absent_deduction=absent_deduction,
        advance_deduction=advance_deduction,
        paid_amount=paid_amount,
    )
    database.add(salary_payment)

    expense_booking_date = salary_month_end - timedelta(days=1)

    expense = Expense(
        expense_date=expense_booking_date,
        expense_type="Salary",
        note=employee.name,
        amount=paid_amount,
    )
    database.add(expense)
    database.commit()
    database.refresh(salary_payment)

    return {
        "status": "ok",
        "payment": {
            "id": salary_payment.id,
            "employee_id": employee.id,
            "employee_name": employee.name,
            "payment_date": parsed_payment_date.date().isoformat(),
            "salary_month": salary_month_start.strftime("%B %Y"),
            "total_days": total_days,
            "working_days": working_days,
            "present_days": present_days,
            "absent_days": absent_days,
            "paid_leave_days": 1,
            "deducted_absent_days": payable_absent_days,
            "absent_deduction": absent_deduction,
            "advance_deduction": advance_deduction,
            "paid_amount": paid_amount,
        },
    }


@router.post("/expenses")
def add_expense(
    expense_date: str,
    expense_type: str,
    amount: float,
    note: str = "",
    database=Depends(db),
):
    expense_type = (expense_type or "").strip()
    if expense_type not in EXPENSE_TYPES:
        raise HTTPException(status_code=400, detail="Invalid expense type")

    if amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than zero")

    parsed_date = datetime.strptime(expense_date, "%Y-%m-%d")
    row = Expense(
        expense_date=parsed_date,
        expense_type=expense_type,
        note=(note or "").strip() or None,
        amount=amount,
    )
    database.add(row)
    database.commit()
    database.refresh(row)

    return {
        "status": "ok",
        "expense": {
            "id": row.id,
            "expense_date": row.expense_date.date().isoformat(),
            "expense_type": row.expense_type,
            "note": row.note,
            "amount": row.amount,
        },
    }


@router.get("/dashboard")
def dashboard(database=Depends(db)):
    invoice_outstanding = sum(
        (amount or 0) - (paid_amount or 0)
        for amount, paid_amount in database.query(Invoice.amount, Invoice.paid_amount).all()
    )

    ledger_rows = (
        get_credit_summary(database)
    )
    ledger_outstanding = sum((row["outstanding"] or 0) for row in ledger_rows)
    total_outstanding = ledger_outstanding if ledger_rows else invoice_outstanding

    stock_rows = (
        database.query(StockEntry.stock_date, StockEntry.stock_count, StockEntry.created_at, StockEntry.id)
        .order_by(StockEntry.stock_date.desc(), StockEntry.created_at.desc(), StockEntry.id.desc())
        .all()
    )
    unique_by_day = []
    seen_dates = set()
    for row in stock_rows:
        day_key = row.stock_date.date().isoformat()
        if day_key in seen_dates:
            continue
        seen_dates.add(day_key)
        unique_by_day.append(row)
        if len(unique_by_day) >= 30:
            break

    last_7 = unique_by_day[:7]
    average_stock_7_days = (
        sum(row.stock_count for row in last_7) / len(last_7) if last_7 else 0
    )
    stock_by_day = {row.stock_date.date().isoformat(): row.stock_count for row in unique_by_day}
    yesterday_key = (now_ist() - timedelta(days=1)).date().isoformat()
    previous_day_closing_stock = stock_by_day.get(yesterday_key, 0)
    stock_history = [
        {
            "stock_date": row.stock_date.date().isoformat(),
            "stock_count": row.stock_count,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in reversed(unique_by_day[:14])
    ]
    current_month_start, next_month_start = month_bounds(datetime.utcnow())
    previous_month_start, previous_month_end = previous_month_bounds(datetime.utcnow())
    salary_reference_date = now_ist()
    salary_month_start = salary_cycle_month(salary_reference_date)
    _, salary_month_end = month_bounds(salary_month_start)
    current_moc_month_start = current_moc_target_month(salary_reference_date)

    current_month_total, current_month_breakdown, current_month_rows = sum_expenses_in_range(
        database, current_month_start, next_month_start
    )
    current_month_advance_total = sum_employee_advances_in_range(
        database, current_month_start, next_month_start
    )
    if current_month_advance_total:
        current_month_total += current_month_advance_total
        current_month_breakdown = sorted(
            [
                *current_month_breakdown,
                {"type": "Salary Advance", "amount": current_month_advance_total},
            ],
            key=lambda item: item["amount"],
            reverse=True,
        )
    previous_month_total, _, _ = sum_expenses_in_range(
        database, previous_month_start, previous_month_end
    )
    recent_expenses = [
        {
            "id": row.id,
            "expense_date": row.expense_date.date().isoformat(),
            "expense_type": row.expense_type,
            "note": row.note,
            "amount": row.amount,
        }
        for row in current_month_rows[:8]
    ]
    employees = database.query(Employee).order_by(Employee.name.asc()).all()
    outstanding_advances, total_deductions, salary_month_advances = build_employee_advance_maps(
        database, salary_month_start, salary_month_end
    )
    employee_rows = []
    for employee in employees:
        outstanding_advance = max(
            (outstanding_advances.get(employee.id, 0) or 0) - (total_deductions.get(employee.id, 0) or 0),
            0,
        )
        row = serialize_employee(employee, outstanding_advance)
        row["salary_month_advance"] = salary_month_advances.get(employee.id, 0) or 0
        row["salary_cycle_paid"] = employee_salary_paid_for_cycle(database, employee.id, salary_month_start)
        employee_rows.append(row)
    recent_advances = (
        database.query(EmployeeAdvance)
        .order_by(EmployeeAdvance.advance_date.desc(), EmployeeAdvance.id.desc())
        .limit(8)
        .all()
    )
    employee_name_map = {employee.id: employee.name for employee in employees}
    previous_moc = (
        database.query(MOCEntry)
        .filter(MOCEntry.moc_month == current_moc_month_start)
        .order_by(MOCEntry.id.desc())
        .first()
    )
    _, current_moc_month_end = month_bounds(current_moc_month_start)
    current_moc_expense_total, _, _ = sum_expenses_in_range(
        database, current_moc_month_start, current_moc_month_end
    )
    earlier_month_start, earlier_month_end = previous_month_bounds(current_moc_month_start)
    previous_to_previous_moc = (
        database.query(MOCEntry)
        .filter(MOCEntry.moc_month == earlier_month_start)
        .order_by(MOCEntry.id.desc())
        .first()
    )
    prev_moc_sales = previous_moc.total_sales if previous_moc else 0
    prev_moc_icd_sales = previous_moc.total_icd_sales if previous_moc else 0
    prev_moc_discount = previous_moc.total_discount if previous_moc else 0
    prev_moc_closing_stock = previous_moc.closing_stock_value if previous_moc else 0
    prev_moc_margin = prev_moc_sales * 0.039
    prev_moc_icd_profit = prev_moc_icd_sales * 0.14
    prev_moc_profit = prev_moc_margin - current_moc_expense_total - prev_moc_discount
    growth_percent = None
    profit_growth_percent = None
    if previous_to_previous_moc and previous_to_previous_moc.total_sales:
        growth_percent = (
            (prev_moc_sales - previous_to_previous_moc.total_sales)
            / previous_to_previous_moc.total_sales
        ) * 100
        previous_to_previous_margin = previous_to_previous_moc.total_sales * 0.039
        previous_to_previous_profit = (
            previous_to_previous_margin
            - sum_expenses_in_range(database, earlier_month_start, earlier_month_end)[0]
            - (previous_to_previous_moc.total_discount or 0)
        )
        if previous_to_previous_profit != 0:
            profit_growth_percent = (
                (prev_moc_profit - previous_to_previous_profit) / abs(previous_to_previous_profit)
            ) * 100

    return {
        "total_outstanding": total_outstanding,
        "average_stock_7_days": average_stock_7_days,
        "previous_day_closing_stock": previous_day_closing_stock,
        "stock_history": stock_history,
        "active_dispatches": database.query(Dispatch).filter(Dispatch.status == "active").count(),
        "current_month_expenses": current_month_total,
        "previous_month_expenses": previous_month_total,
        "prev_moc_month": current_moc_month_start.strftime("%B %Y"),
        "prev_moc_sales": prev_moc_sales,
        "prev_moc_icd_sales": prev_moc_icd_sales,
        "prev_moc_discount": prev_moc_discount,
        "prev_moc_closing_stock": prev_moc_closing_stock,
        "prev_moc_margin": prev_moc_margin,
        "prev_moc_icd_profit": prev_moc_icd_profit,
        "prev_moc_profit": prev_moc_profit,
        "prev_moc_growth_percent": growth_percent,
        "prev_moc_profit_growth_percent": profit_growth_percent,
        "expense_breakdown": current_month_breakdown,
        "recent_expenses": recent_expenses,
        "expense_types": EXPENSE_TYPES,
        "employee_roles": EMPLOYEE_ROLES,
        "employees": employee_rows,
        "salary_window_open": 1 <= salary_reference_date.day <= 10,
        "salary_target_month": salary_month_start.strftime("%B %Y"),
        "salary_total_days": total_days_in_month(salary_month_start, salary_month_end),
        "salary_working_days": working_days_in_month(salary_month_start, salary_month_end),
        "paid_leave_days": 1,
        "recent_advances": [
            {
                "id": row.id,
                "employee_id": row.employee_id,
                "employee_name": employee_name_map.get(row.employee_id, "Employee"),
                "advance_date": row.advance_date.date().isoformat(),
                "amount": row.amount,
                "note": row.note,
            }
            for row in recent_advances
        ],
    }
