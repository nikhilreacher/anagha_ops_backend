
from fastapi import APIRouter, Depends, HTTPException
from database import SessionLocal
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
from datetime import datetime, timedelta

router = APIRouter()

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

@router.get("/credit")
def credit(database=Depends(db)):
    shops = database.query(Shop).all()
    res = []
    for shop in shops:
        ledger_rows = (
            database.query(Ledger)
            .filter(Ledger.shop_id == shop.id)
            .filter(Ledger.balance.isnot(None))
            .filter(Ledger.balance > 0)
            .all()
        )

        if not ledger_rows:
            continue

        total = 0
        max_age = 0
        bills = []

        for row in ledger_rows:
            balance = row.balance or 0
            total += balance
            age = (datetime.utcnow() - row.bill_date).days if row.bill_date else 0
            max_age = max(max_age, age)
            bills.append(
                {
                    "bill_no": row.bill_no,
                    "bill_date": row.bill_date.date().isoformat() if row.bill_date else None,
                    "delivery_date": row.delivery_date.date().isoformat() if row.delivery_date else None,
                    "balance": balance,
                    "remarks": row.remarks,
                }
            )

        bills.sort(
            key=lambda item: (
                item["bill_date"] is None,
                item["bill_date"] or "",
                item["bill_no"],
            )
        )

        res.append(
            {
                "shop_id": shop.id,
                "shop": shop.name,
                "beat": shop.beat,
                "outstanding": total,
                "max_age": max_age,
                "bills": bills,
            }
        )

    res.sort(key=lambda x: (x["max_age"], x["outstanding"]), reverse=True)
    return res


def month_bounds(reference_date: datetime):
    month_start = reference_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_month = (month_start + timedelta(days=32)).replace(day=1)
    return month_start, next_month


def previous_month_bounds(reference_date: datetime):
    current_month_start, _ = month_bounds(reference_date)
    previous_month_last_day = current_month_start - timedelta(days=1)
    previous_month_start = previous_month_last_day.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return previous_month_start, current_month_start


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


def serialize_employee(employee, outstanding_advance=0):
    return {
        "id": employee.id,
        "name": employee.name,
        "role": employee.role,
        "phone": employee.phone,
        "salary": employee.salary,
        "outstanding_advance": outstanding_advance,
        "previous_month_advance": 0,
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


@router.get("/employees")
def list_employees(database=Depends(db)):
    employees = database.query(Employee).order_by(Employee.name.asc()).all()
    return [serialize_employee(employee, get_employee_outstanding_advance(database, employee.id)) for employee in employees]


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
            "id": row.id,
            "employee_id": employee.id,
            "employee_name": employee.name,
            "advance_date": row.advance_date.date().isoformat(),
            "amount": row.amount,
            "note": row.note,
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
    if not (1 <= parsed_payment_date.day <= 20):
        raise HTTPException(status_code=400, detail="Salary payment is allowed only from 1st to 20th")

    previous_month_start, current_month_start = previous_month_bounds(parsed_payment_date)
    working_days = working_days_in_month(previous_month_start, current_month_start)
    total_days = total_days_in_month(previous_month_start, current_month_start)
    if absent_days > working_days:
        raise HTTPException(status_code=400, detail="Present and absent days exceed previous month's working days")
    present_days = working_days - absent_days

    payable_absent_days = max(absent_days - 1, 0)
    daily_salary = (employee.salary or 0) / total_days if employee.salary and total_days else 0
    absent_deduction = daily_salary * payable_absent_days
    available_after_absence = max((employee.salary or 0) - absent_deduction, 0)
    previous_month_advances = get_employee_advances_for_range(
        database, employee.id, previous_month_start, current_month_start
    )
    advance_deduction = min(previous_month_advances, available_after_absence)
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

    expense = Expense(
        expense_date=parsed_payment_date,
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
            "salary_month": previous_month_start.strftime("%B %Y"),
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
    invoices = database.query(Invoice).all()
    invoice_outstanding = sum(
        (invoice.amount or 0) - (invoice.paid_amount or 0) for invoice in invoices
    )

    ledger_rows = (
        database.query(Ledger)
        .filter(Ledger.balance.isnot(None))
        .filter(Ledger.balance > 0)
        .all()
    )
    ledger_outstanding = sum((row.balance or 0) for row in ledger_rows)
    total_outstanding = ledger_outstanding if ledger_rows else invoice_outstanding

    stock_rows = database.query(StockEntry).order_by(StockEntry.stock_date.desc()).all()
    unique_by_day = []
    seen_dates = set()
    for row in stock_rows:
        day_key = row.stock_date.date().isoformat()
        if day_key in seen_dates:
            continue
        seen_dates.add(day_key)
        unique_by_day.append(row)

    last_7 = unique_by_day[:7]
    average_stock_7_days = (
        sum(row.stock_count for row in last_7) / len(last_7) if last_7 else 0
    )
    previous_day_closing_stock = last_7[1].stock_count if len(last_7) > 1 else (
        last_7[0].stock_count if last_7 else 0
    )
    current_month_start, next_month_start = month_bounds(datetime.utcnow())
    previous_month_start, previous_month_end = previous_month_bounds(datetime.utcnow())

    current_month_total, current_month_breakdown, current_month_rows = sum_expenses_in_range(
        database, current_month_start, next_month_start
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
    employee_rows = []
    for employee in employees:
        row = serialize_employee(employee, get_employee_outstanding_advance(database, employee.id))
        row["previous_month_advance"] = get_employee_advances_for_range(
            database, employee.id, previous_month_start, previous_month_end
        )
        employee_rows.append(row)
    recent_advances = (
        database.query(EmployeeAdvance)
        .order_by(EmployeeAdvance.advance_date.desc(), EmployeeAdvance.id.desc())
        .all()
    )
    employee_name_map = {employee.id: employee.name for employee in employees}
    previous_moc = (
        database.query(MOCEntry)
        .filter(MOCEntry.moc_month == previous_month_start)
        .order_by(MOCEntry.id.desc())
        .first()
    )
    earlier_month_start, earlier_month_end = previous_month_bounds(previous_month_start)
    previous_to_previous_moc = (
        database.query(MOCEntry)
        .filter(MOCEntry.moc_month == earlier_month_start)
        .order_by(MOCEntry.id.desc())
        .first()
    )
    prev_moc_sales = previous_moc.total_sales if previous_moc else 0
    prev_moc_discount = previous_moc.total_discount if previous_moc else 0
    prev_moc_margin = prev_moc_sales * 0.039
    prev_moc_profit = prev_moc_margin - previous_month_total - prev_moc_discount
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
        "active_dispatches": database.query(Dispatch).filter(Dispatch.status == "active").count(),
        "current_month_expenses": current_month_total,
        "previous_month_expenses": previous_month_total,
        "prev_moc_month": previous_month_start.strftime("%B %Y"),
        "prev_moc_sales": prev_moc_sales,
        "prev_moc_discount": prev_moc_discount,
        "prev_moc_margin": prev_moc_margin,
        "prev_moc_profit": prev_moc_profit,
        "prev_moc_growth_percent": growth_percent,
        "prev_moc_profit_growth_percent": profit_growth_percent,
        "expense_breakdown": current_month_breakdown,
        "recent_expenses": recent_expenses,
        "expense_types": EXPENSE_TYPES,
        "employee_roles": EMPLOYEE_ROLES,
        "employees": employee_rows,
        "salary_window_open": 1 <= datetime.utcnow().day <= 20,
        "salary_target_month": previous_month_start.strftime("%B %Y"),
        "salary_total_days": total_days_in_month(previous_month_start, previous_month_end),
        "salary_working_days": working_days_in_month(previous_month_start, previous_month_end),
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
            for row in recent_advances[:8]
        ],
    }
