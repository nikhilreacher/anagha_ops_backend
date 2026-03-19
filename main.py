
from fastapi import FastAPI
from sqlalchemy import inspect, text
import hashlib
from database import Base, engine
from routes import shop, dispatch, payment, admin, route, auth

Base.metadata.create_all(bind=engine)


def run_shop_beat_migration():
    inspector = inspect(engine)
    shop_columns = {column["name"] for column in inspector.get_columns("shops")}

    with engine.begin() as connection:
        if "beat" not in shop_columns:
            connection.execute(text("ALTER TABLE shops ADD COLUMN beat VARCHAR"))

        # Backfill beat values from the old numeric route_id column if needed.
        shop_columns = {column["name"] for column in inspect(engine).get_columns("shops")}
        if "route_id" in shop_columns:
            connection.execute(
                text(
                    """
                    UPDATE shops
                    SET beat = 'beat' || route_id
                    WHERE route_id IS NOT NULL
                      AND (beat IS NULL OR beat = '')
                    """
                )
            )


run_shop_beat_migration()


def run_dispatch_migration():
    inspector = inspect(engine)
    dispatch_columns = {column["name"] for column in inspector.get_columns("dispatches")}
    ledger_columns = {column["name"] for column in inspector.get_columns("ledger")}
    table_names = set(inspector.get_table_names())
    return_columns = {column["name"] for column in inspector.get_columns("return_tasks")} if "return_tasks" in table_names else set()

    with engine.begin() as connection:
        if "status" not in dispatch_columns:
            connection.execute(text("ALTER TABLE dispatches ADD COLUMN status VARCHAR DEFAULT 'active'"))
        if "returns_checked" not in dispatch_columns:
            connection.execute(text("ALTER TABLE dispatches ADD COLUMN returns_checked INTEGER DEFAULT 0"))
        if "new_credits_checked" not in dispatch_columns:
            connection.execute(text("ALTER TABLE dispatches ADD COLUMN new_credits_checked INTEGER DEFAULT 0"))
        if "new_credit_total" not in dispatch_columns:
            connection.execute(text("ALTER TABLE dispatches ADD COLUMN new_credit_total FLOAT DEFAULT 0"))
        if "close_notes" not in dispatch_columns:
            connection.execute(text("ALTER TABLE dispatches ADD COLUMN close_notes VARCHAR"))
        if "closed_at" not in dispatch_columns:
            connection.execute(text("ALTER TABLE dispatches ADD COLUMN closed_at DATETIME"))
        if "dispatch_id" not in ledger_columns:
            connection.execute(text("ALTER TABLE ledger ADD COLUMN dispatch_id INTEGER"))
        if "return_tasks" not in table_names:
            connection.execute(
                text(
                    """
                    CREATE TABLE return_tasks (
                        id INTEGER PRIMARY KEY,
                        dispatch_id INTEGER NOT NULL,
                        beat VARCHAR NOT NULL,
                        created_at DATETIME NOT NULL,
                        status VARCHAR NOT NULL DEFAULT 'pending',
                        resolved_at DATETIME
                    )
                    """
                )
            )
        if "stock_entries" not in table_names:
            connection.execute(
                text(
                    """
                    CREATE TABLE stock_entries (
                        id INTEGER PRIMARY KEY,
                        stock_date DATETIME NOT NULL,
                        stock_count FLOAT NOT NULL,
                        created_at DATETIME NOT NULL
                    )
                    """
                )
            )
        if "expenses" not in table_names:
            connection.execute(
                text(
                    """
                    CREATE TABLE expenses (
                        id INTEGER PRIMARY KEY,
                        expense_date DATETIME NOT NULL,
                        expense_type VARCHAR NOT NULL,
                        note VARCHAR,
                        amount FLOAT NOT NULL,
                        created_at DATETIME NOT NULL
                    )
                    """
                )
            )
        if "employees" not in table_names:
            connection.execute(
                text(
                    """
                    CREATE TABLE employees (
                        id INTEGER PRIMARY KEY,
                        name VARCHAR NOT NULL,
                        role VARCHAR NOT NULL,
                        phone VARCHAR,
                        salary FLOAT NOT NULL,
                        created_at DATETIME NOT NULL
                    )
                    """
                )
            )
        if "employee_advances" not in table_names:
            connection.execute(
                text(
                    """
                    CREATE TABLE employee_advances (
                        id INTEGER PRIMARY KEY,
                        employee_id INTEGER NOT NULL,
                        advance_date DATETIME NOT NULL,
                        amount FLOAT NOT NULL,
                        note VARCHAR,
                        created_at DATETIME NOT NULL
                    )
                    """
                )
            )
        if "salary_payments" not in table_names:
            connection.execute(
                text(
                    """
                    CREATE TABLE salary_payments (
                        id INTEGER PRIMARY KEY,
                        employee_id INTEGER NOT NULL,
                        payment_date DATETIME NOT NULL,
                        present_days FLOAT NOT NULL,
                        absent_days FLOAT NOT NULL,
                        absent_deduction FLOAT NOT NULL,
                        advance_deduction FLOAT NOT NULL,
                        paid_amount FLOAT NOT NULL,
                        created_at DATETIME NOT NULL
                    )
                    """
                )
            )
        if "moc_entries" not in table_names:
            connection.execute(
                text(
                    """
                    CREATE TABLE moc_entries (
                        id INTEGER PRIMARY KEY,
                        moc_month DATETIME NOT NULL,
                        total_sales FLOAT NOT NULL,
                        total_discount FLOAT NOT NULL,
                        created_at DATETIME NOT NULL
                    )
                    """
                )
            )
        if "app_users" not in table_names:
            connection.execute(
                text(
                    """
                    CREATE TABLE app_users (
                        id INTEGER PRIMARY KEY,
                        username VARCHAR NOT NULL UNIQUE,
                        password_hash VARCHAR NOT NULL,
                        role VARCHAR NOT NULL,
                        label VARCHAR NOT NULL,
                        created_at DATETIME NOT NULL
                    )
                    """
                )
            )
        if "return_tasks" in table_names and "route_label" not in return_columns:
            connection.execute(text("ALTER TABLE return_tasks ADD COLUMN route_label VARCHAR"))

        connection.execute(
            text(
                """
                UPDATE dispatches
                SET status = 'active'
                WHERE status IS NULL OR status = ''
                """
            )
        )
        connection.execute(
            text(
                """
                UPDATE dispatches
                SET new_credit_total = 0
                WHERE new_credit_total IS NULL
                """
            )
        )
        default_users = [
            ("admin", hashlib.sha256("admin123".encode()).hexdigest(), "admin", "Admin"),
            ("it", hashlib.sha256("it123".encode()).hexdigest(), "it", "IT Team"),
            ("delivery", hashlib.sha256("delivery123".encode()).hexdigest(), "delivery", "Delivery Team"),
        ]
        for username, password_hash, role_value, label in default_users:
            connection.execute(
                text(
                    """
                    INSERT INTO app_users (username, password_hash, role, label, created_at)
                    SELECT :username, :password_hash, :role, :label, CURRENT_TIMESTAMP
                    WHERE NOT EXISTS (
                        SELECT 1 FROM app_users WHERE username = :username
                    )
                    """
                ),
                {
                    "username": username,
                    "password_hash": password_hash,
                    "role": role_value,
                    "label": label,
                },
            )


run_dispatch_migration()

app = FastAPI()

from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(shop.router, prefix="/shops")
app.include_router(dispatch.router, prefix="/dispatch")
app.include_router(payment.router, prefix="/payments")
app.include_router(admin.router, prefix="/admin")
app.include_router(route.router, prefix="/routes")
app.include_router(auth.router, prefix="/auth")

@app.get("/dashboard")
def dashboard_root_proxy():
    return admin.dashboard()

@app.get("/")
def root():
    return {"msg": "Final Backend Running"}
