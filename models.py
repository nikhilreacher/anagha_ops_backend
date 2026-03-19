from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String

from database import Base


class Route(Base):
    __tablename__ = "routes"

    id = Column(Integer, primary_key=True)
    name = Column(String)


class Shop(Base):
    __tablename__ = "shops"

    id = Column(Integer, primary_key=True)
    name = Column(String)
    phone = Column(String)
    address = Column(String)
    beat = Column(String)
    lat = Column(Float, nullable=True)
    lon = Column(Float, nullable=True)


class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True)
    shop_id = Column(Integer)
    bill_no = Column(String)
    amount = Column(Float)
    bill_date = Column(DateTime)
    paid_amount = Column(Float, default=0)


class Dispatch(Base):
    __tablename__ = "dispatches"

    id = Column(Integer, primary_key=True)
    beat = Column(String, nullable=False)
    total_bills = Column(Integer, nullable=False)
    total_cases = Column(Integer, nullable=False)
    star_bags_boxes = Column(Integer, nullable=False)
    status = Column(String, default="active", nullable=False)
    returns_checked = Column(Integer, default=0, nullable=False)
    new_credits_checked = Column(Integer, default=0, nullable=False)
    new_credit_total = Column(Float, default=0, nullable=False)
    close_notes = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    closed_at = Column(DateTime, nullable=True)


class ReturnTask(Base):
    __tablename__ = "return_tasks"

    id = Column(Integer, primary_key=True)
    dispatch_id = Column(Integer, ForeignKey("dispatches.id"), nullable=False)
    beat = Column(String, nullable=True)
    route_label = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    status = Column(String, default="pending", nullable=False)
    resolved_at = Column(DateTime, nullable=True)


class StockEntry(Base):
    __tablename__ = "stock_entries"

    id = Column(Integer, primary_key=True)
    stock_date = Column(DateTime, nullable=False)
    stock_count = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Ledger(Base):
    __tablename__ = "ledger"

    bill_no = Column(String, primary_key=True)
    dispatch_id = Column(Integer, ForeignKey("dispatches.id"), nullable=True)
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=True)
    party = Column(String, nullable=False)
    bill_date = Column(DateTime, nullable=True)
    delivery_date = Column(DateTime, nullable=True)
    beat_name = Column(String, nullable=True)
    salesman = Column(String, nullable=True)
    bill_amt = Column(Float, nullable=True)
    paid_amt = Column(Float, nullable=True)
    balance = Column(Float, nullable=True)
    paid_date = Column(DateTime, nullable=True)
    remarks = Column(String, nullable=True)


class Expense(Base):
    __tablename__ = "expenses"

    id = Column(Integer, primary_key=True)
    expense_date = Column(DateTime, nullable=False)
    expense_type = Column(String, nullable=False)
    note = Column(String, nullable=True)
    amount = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Employee(Base):
    __tablename__ = "employees"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    role = Column(String, nullable=False)
    phone = Column(String, nullable=True)
    salary = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class EmployeeAdvance(Base):
    __tablename__ = "employee_advances"

    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    advance_date = Column(DateTime, nullable=False)
    amount = Column(Float, nullable=False)
    note = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class SalaryPayment(Base):
    __tablename__ = "salary_payments"

    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    payment_date = Column(DateTime, nullable=False)
    present_days = Column(Float, nullable=False)
    absent_days = Column(Float, nullable=False)
    absent_deduction = Column(Float, nullable=False)
    advance_deduction = Column(Float, nullable=False)
    paid_amount = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class MOCEntry(Base):
    __tablename__ = "moc_entries"

    id = Column(Integer, primary_key=True)
    moc_month = Column(DateTime, nullable=False)
    total_sales = Column(Float, nullable=False)
    total_discount = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class AppUser(Base):
    __tablename__ = "app_users"

    id = Column(Integer, primary_key=True)
    username = Column(String, nullable=False, unique=True)
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False)
    label = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
