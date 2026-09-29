from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    send_file
)
import sqlite3
from datetime import datetime, date
import re
import os
import secrets
import logging
from io import BytesIO
from functools import wraps
from werkzeug.security import (
    generate_password_hash,
    check_password_hash
)

app = Flask(__name__)

# -----------------------------------------------------
# ERROR LOGGING (file mein save hoga: app_errors.log)
# -----------------------------------------------------

_file_log_handler = logging.FileHandler(
    "app_errors.log"
)

_file_log_handler.setLevel(logging.ERROR)

_file_log_handler.setFormatter(
    logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s"
    )
)

app.logger.addHandler(_file_log_handler)

# -----------------------------------------------------
# SECRET KEY (session encryption ke liye)
# Production mein isse environment variable se set karein:
#   export SECRET_KEY="apni-random-secret-key"
# -----------------------------------------------------

# -----------------------------------------------------
# SECRET KEY (session encryption ke liye)
# Production mein isse environment variable se set karein:
#   export SECRET_KEY="apni-random-secret-key"
# Agar env variable nahi mili, to ek baar generate karke
# local file mein save kar denge (taaki restart pe sabka
# login session invalid na ho jaye).
# -----------------------------------------------------

SECRET_KEY_FILE = "secret_key.txt"


def load_or_create_secret_key():

    env_key = os.environ.get("SECRET_KEY")

    if env_key:
        return env_key

    if os.path.exists(SECRET_KEY_FILE):

        with open(SECRET_KEY_FILE, "r") as f:

            existing_key = f.read().strip()

            if existing_key:
                return existing_key

    new_key = secrets.token_hex(32)

    with open(SECRET_KEY_FILE, "w") as f:
        f.write(new_key)

    return new_key


app.secret_key = load_or_create_secret_key()

DATABASE = "database.db"

DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin123"


# =========================================================
# DATABASE CONNECTION
# =========================================================

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


# =========================================================
# CREATE DATABASE
# =========================================================

def create_database():
    conn = get_db()
    cursor = conn.cursor()

    # -----------------------------------------------------
    # CUSTOMERS
    # -----------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_number TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            mobile TEXT,
            bank_name TEXT,
            ifsc_code TEXT,
            amount TEXT,
            transaction_datetime TEXT,
            utr_number TEXT,
            site TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # -----------------------------------------------------
    # TRANSACTION BATCHES
    # -----------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transaction_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            total_amount REAL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # -----------------------------------------------------
    # TRANSACTIONS
    # -----------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER,
            customer_id INTEGER,
            amount REAL DEFAULT 0,
            transaction_datetime TEXT DEFAULT '',
            utr_number TEXT DEFAULT '',
            site TEXT DEFAULT ''
        )
    """)

    # -----------------------------------------------------
    # CAPITAL
    # -----------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS capital (
            id INTEGER PRIMARY KEY,
            amount REAL DEFAULT 0
        )
    """)

    # हमेशा एक capital record रहेगा
    cursor.execute("""
        SELECT id
        FROM capital
        WHERE id = 1
    """)

    capital_row = cursor.fetchone()

    if capital_row is None:
        cursor.execute("""
            INSERT INTO capital (id, amount)
            VALUES (1, 0)
        """)

    # -----------------------------------------------------
    # CAPITAL HISTORY
    # -----------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS capital_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            amount REAL NOT NULL DEFAULT 0,
            transaction_datetime TEXT NOT NULL,
            site TEXT DEFAULT '',
            payment_type TEXT DEFAULT 'PAID',
            remarks TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # -----------------------------------------------------
    # USERS (login ke liye)
    # -----------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # -----------------------------------------------------
    # RECHARGES (Mobile Recharge)
    # -----------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS recharges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mobile_number TEXT NOT NULL,
            operator TEXT DEFAULT '',
            circle TEXT DEFAULT '',
            amount REAL NOT NULL DEFAULT 0,
            transaction_datetime TEXT NOT NULL,
            remarks TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # -----------------------------------------------------
    # REFUNDS (Transaction Refund)
    # -----------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS refunds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER NOT NULL,
            customer_id INTEGER NOT NULL,
            amount REAL NOT NULL DEFAULT 0,
            transaction_datetime TEXT NOT NULL,
            remarks TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # -----------------------------------------------------
    # RECHARGE REFUNDS
    # -----------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS recharge_refunds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recharge_id INTEGER NOT NULL,
            amount REAL NOT NULL DEFAULT 0,
            transaction_datetime TEXT NOT NULL,
            remarks TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


# =========================================================
# CREATE DEFAULT ADMIN USER (agar koi user nahi hai)
# =========================================================

def ensure_default_admin():

    conn = get_db()

    cursor = conn.cursor()

    cursor.execute("""
        SELECT COUNT(*) AS total
        FROM users
    """)

    total_users = cursor.fetchone()["total"]

    if total_users == 0:

        password_hash = generate_password_hash(
            DEFAULT_ADMIN_PASSWORD
        )

        cursor.execute("""
            INSERT INTO users (username, password_hash)
            VALUES (?, ?)
        """, (
            DEFAULT_ADMIN_USERNAME,
            password_hash
        ))

        conn.commit()

        print(
            "========================================\n"
            "DEFAULT LOGIN CREATED:\n"
            f"  Username: {DEFAULT_ADMIN_USERNAME}\n"
            f"  Password: {DEFAULT_ADMIN_PASSWORD}\n"
            "Please login and change this password "
            "immediately from the Change Password page.\n"
            "========================================"
        )

    conn.close()


# =========================================================
# DATABASE MIGRATION
# =========================================================

def add_missing_columns():
    conn = get_db()
    cursor = conn.cursor()

    # -----------------------------------------------------
    # CUSTOMER COLUMNS
    # -----------------------------------------------------

    cursor.execute("PRAGMA table_info(customers)")

    customer_columns = [
        row["name"]
        for row in cursor.fetchall()
    ]

    customer_required = {
        "bank_name": "TEXT",
        "ifsc_code": "TEXT",
        "amount": "TEXT",
        "transaction_datetime": "TEXT",
        "utr_number": "TEXT",
        "site": "TEXT"
    }

    for column_name, column_type in customer_required.items():

        if column_name not in customer_columns:
            cursor.execute(
                f"""
                ALTER TABLE customers
                ADD COLUMN {column_name} {column_type}
                """
            )

    # -----------------------------------------------------
    # TRANSACTION COLUMNS
    # -----------------------------------------------------

    cursor.execute("PRAGMA table_info(transactions)")

    transaction_columns = [
        row["name"]
        for row in cursor.fetchall()
    ]

    transaction_required = {
        "batch_id": "INTEGER",
        "customer_id": "INTEGER",
        "amount": "REAL",
        "transaction_datetime": "TEXT",
        "utr_number": "TEXT",
        "site": "TEXT"

    }

    for column_name, column_type in transaction_required.items():

        if column_name not in transaction_columns:
            cursor.execute(
                f"""
                ALTER TABLE transactions
                ADD COLUMN {column_name} {column_type}
                """
            )

    # -----------------------------------------------------
    # CAPITAL TABLE
    # -----------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS capital (
            id INTEGER PRIMARY KEY,
            amount REAL DEFAULT 0
        )
    """)

    cursor.execute("""
        SELECT id
        FROM capital
        WHERE id = 1
    """)

    if cursor.fetchone() is None:
        cursor.execute("""
            INSERT INTO capital (id, amount)
            VALUES (1, 0)
        """)

    # -----------------------------------------------------
    # CAPITAL HISTORY TABLE
    # -----------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS capital_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            amount REAL NOT NULL DEFAULT 0,
            transaction_datetime TEXT NOT NULL,
            site TEXT DEFAULT '',
            payment_type TEXT DEFAULT 'PAID',
            remarks TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # पुराने database में नए columns add होंगे
    cursor.execute("PRAGMA table_info(capital_history)")

    capital_history_columns = [
        row["name"]
        for row in cursor.fetchall()
    ]

    capital_history_required = {
        "payment_type": "TEXT DEFAULT 'PAID'",
        "remarks": "TEXT DEFAULT ''"
    }

    for column_name, column_type in capital_history_required.items():

        if column_name not in capital_history_columns:
            cursor.execute(
                f"""
                ALTER TABLE capital_history
                ADD COLUMN {column_name} {column_type}
                """
            )

    # पुराने records में payment_type खाली हो तो PAID कर दें
    cursor.execute("""
        UPDATE capital_history
        SET payment_type = 'PAID'
        WHERE payment_type IS NULL
           OR TRIM(payment_type) = ''
    """)

    # पुराने records में remarks खाली कर दें
    cursor.execute("""
        UPDATE capital_history
        SET remarks = ''
        WHERE remarks IS NULL
    """)

    # -----------------------------------------------------
    # RECHARGES TABLE (agar pehle se ho to naye columns)
    # -----------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS recharges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mobile_number TEXT NOT NULL,
            operator TEXT DEFAULT '',
            circle TEXT DEFAULT '',
            amount REAL NOT NULL DEFAULT 0,
            transaction_datetime TEXT NOT NULL,
            remarks TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("PRAGMA table_info(recharges)")

    recharge_columns = [
        row["name"]
        for row in cursor.fetchall()
    ]

    recharge_required = {
        "operator": "TEXT DEFAULT ''",
        "circle": "TEXT DEFAULT ''",
        "remarks": "TEXT DEFAULT ''"
    }

    for column_name, column_type in recharge_required.items():

        if column_name not in recharge_columns:
            cursor.execute(
                f"""
                ALTER TABLE recharges
                ADD COLUMN {column_name} {column_type}
                """
            )

    # -----------------------------------------------------
    # REFUNDS TABLE (agar pehle se DB purana ho)
    # -----------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS refunds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER NOT NULL,
            customer_id INTEGER NOT NULL,
            amount REAL NOT NULL DEFAULT 0,
            transaction_datetime TEXT NOT NULL,
            remarks TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # -----------------------------------------------------
    # RECHARGE REFUNDS TABLE (agar pehle se DB purana ho)
    # -----------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS recharge_refunds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recharge_id INTEGER NOT NULL,
            amount REAL NOT NULL DEFAULT 0,
            transaction_datetime TEXT NOT NULL,
            remarks TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


# =========================================================
# CAPITAL FUNCTIONS
# =========================================================

def get_capital():

    conn = get_db()

    row = conn.execute("""
        SELECT amount
        FROM capital
        WHERE id = 1
    """).fetchone()

    conn.close()

    if row is None:
        return 0.0

    return float(row["amount"] or 0)


def set_capital(amount):

    amount = float(amount or 0)

    if amount < 0:
        amount = 0

    conn = get_db()

    conn.execute("""
        UPDATE capital
        SET amount = ?
        WHERE id = 1
    """, (amount,))

    conn.commit()
    conn.close()


# =========================================================
# DATE NORMALIZATION
# =========================================================

def normalize_transaction_date(value):

    if value is None:
        return None

    value = str(value).strip()

    if not value:
        return None

    value = " ".join(value.split())

    value_for_parse = value.replace("T", " ")

    if value_for_parse.endswith("Z"):
        value_for_parse = value_for_parse[:-1].strip()

    formats = [

        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S.%f",

        "%Y-%m-%d %I:%M:%S %p",
        "%Y-%m-%d %I:%M %p",
        "%Y-%m-%d %I:%M:%S.%f %p",

        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y %H:%M:%S.%f",

        "%d/%m/%Y %I:%M:%S %p",
        "%d/%m/%Y %I:%M %p",

        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%d-%m-%Y %H:%M:%S.%f",

        "%d-%m-%Y %I:%M:%S %p",
        "%d-%m-%Y %I:%M %p",

        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y %H:%M",
        "%d.%m.%Y %H:%M:%S.%f",

        "%d.%m.%Y %I:%M:%S %p",
        "%d.%m.%Y %I:%M %p",

        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",

        "%m/%d/%Y %I:%M:%S %p",
        "%m/%d/%Y %I:%M %p",

        "%m-%d-%Y %H:%M:%S",
        "%m-%d-%Y %H:%M",

        "%m-%d-%Y %I:%M:%S %p",
        "%m-%d-%Y %I:%M %p",

        "%Y-%m-%d",

        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d.%m.%Y",

        "%m/%d/%Y",
        "%m-%d-%Y",
        "%m.%d.%Y"
    ]

    for fmt in formats:

        try:

            parsed = datetime.strptime(
                value_for_parse,
                fmt
            )

            return parsed.strftime("%Y-%m-%d")

        except ValueError:
            pass

    # -----------------------------------------------------
    # YYYY-MM-DD
    # -----------------------------------------------------

    match = re.search(
        r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b",
        value
    )

    if match:

        try:

            year = int(match.group(1))
            month = int(match.group(2))
            day = int(match.group(3))

            parsed = date(
                year,
                month,
                day
            )

            return parsed.strftime("%Y-%m-%d")

        except ValueError:
            pass

    # -----------------------------------------------------
    # DD/MM/YYYY
    # -----------------------------------------------------

    match = re.search(
        r"\b(\d{1,2})[\/\-.](\d{1,2})[\/\-.](\d{4})\b",
        value
    )

    if match:

        first = int(match.group(1))
        second = int(match.group(2))
        year = int(match.group(3))

        try:

            parsed = date(
                year,
                second,
                first
            )

            return parsed.strftime("%Y-%m-%d")

        except ValueError:
            pass

        try:

            parsed = date(
                year,
                first,
                second
            )

            return parsed.strftime("%Y-%m-%d")

        except ValueError:
            pass

    return None


# =========================================================
# VALIDATION HELPERS
# =========================================================

def is_valid_mobile_number(mobile):

    if not mobile:
        return True

    return bool(
        re.fullmatch(
            r"[6-9]\d{9}",
            mobile.strip()
        )
    )


def is_valid_ifsc_code(ifsc):

    if not ifsc:
        return True

    return bool(
        re.fullmatch(
            r"[A-Z]{4}0[A-Z0-9]{6}",
            ifsc.strip().upper()
        )
    )


# =========================================================
# PARSE FULL DATETIME (ledger sorting ke liye)
# =========================================================

def parse_datetime_for_sort(value):

    if value is None:
        return None

    value = str(value).strip()

    if not value:
        return None

    value_for_parse = value.replace("T", " ")

    formats = [

        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",

        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",

        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",

        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y %H:%M",

        "%Y-%m-%d %I:%M:%S %p",
        "%Y-%m-%d %I:%M %p",

        "%d/%m/%Y %I:%M:%S %p",
        "%d/%m/%Y %I:%M %p",

        "%d-%m-%Y %I:%M:%S %p",
        "%d-%m-%Y %I:%M %p"
    ]

    for fmt in formats:

        try:

            return datetime.strptime(
                value_for_parse,
                fmt
            )

        except ValueError:
            pass

    # Agar time nahi mila, to sirf date parse karke
    # midnight (00:00) time maan lete hain

    normalized_date = normalize_transaction_date(
        value
    )

    if normalized_date:

        try:

            return datetime.strptime(
                normalized_date,
                "%Y-%m-%d"
            )

        except ValueError:
            return None

    return None


# =========================================================
# GET TRANSACTIONS BY DATE
# =========================================================

def get_transactions_by_date(selected_date):

    normalized_selected_date = normalize_transaction_date(
        selected_date
    )

    if not normalized_selected_date:
        normalized_selected_date = str(
            selected_date
        ).strip()

    conn = get_db()

    rows = conn.execute("""
        SELECT
            transactions.id,
            transactions.batch_id,
            transactions.customer_id,
            transactions.amount,
            transactions.transaction_datetime,
            transactions.utr_number,
            transactions.site,

            customers.account_number,
            customers.name,
            customers.mobile,
            customers.bank_name

        FROM transactions

        LEFT JOIN customers
            ON customers.id = transactions.customer_id

        ORDER BY transactions.id DESC
    """).fetchall()

    conn.close()

    matched_rows = []

    for row in rows:

        saved_datetime = row["transaction_datetime"]

        transaction_date = normalize_transaction_date(
            saved_datetime
        )

        if (
            transaction_date is not None
            and transaction_date == normalized_selected_date
        ):

            matched_rows.append(row)

    return matched_rows


# =========================================================
# GET TRANSACTIONS BY DATE RANGE
# =========================================================

def get_transactions_by_date_range(start_date, end_date):

    start_normalized = normalize_transaction_date(
        start_date
    )

    end_normalized = normalize_transaction_date(
        end_date
    )

    if not start_normalized or not end_normalized:
        return []

    conn = get_db()

    rows = conn.execute("""
        SELECT
            transactions.id,
            transactions.batch_id,
            transactions.customer_id,
            transactions.amount,
            transactions.transaction_datetime,
            transactions.utr_number,
            transactions.site,
            customers.account_number,
            customers.name,
            customers.mobile,
            customers.bank_name
        FROM transactions
        LEFT JOIN customers
            ON customers.id = transactions.customer_id
        ORDER BY transactions.id DESC
    """).fetchall()

    conn.close()

    matched_rows = []

    for row in rows:

        saved_datetime = row[
            "transaction_datetime"
        ]

        transaction_date = normalize_transaction_date(
            saved_datetime
        )

        if not transaction_date:
            continue

        if (
            start_normalized
            <= transaction_date
            <= end_normalized
        ):
            matched_rows.append(row)

    return matched_rows


# =========================================================
# ALL CUSTOMERS
# =========================================================

def get_all_customers():

    conn = get_db()

    customers = conn.execute("""
        SELECT
            id,
            account_number,
            name,
            mobile,
            bank_name,
            ifsc_code,
            amount,
            transaction_datetime,
            utr_number,
            site,
            created_at

        FROM customers

        ORDER BY id DESC
    """).fetchall()

    conn.close()

    return customers


# =========================================================
# SINGLE CUSTOMER
# =========================================================

def get_customer(customer_id):

    conn = get_db()

    customer = conn.execute("""
        SELECT
            id,
            account_number,
            name,
            mobile,
            bank_name,
            ifsc_code,
            amount,
            transaction_datetime,
            utr_number,
            site,
            created_at

        FROM customers

        WHERE id = ?
    """, (customer_id,)).fetchone()

    conn.close()

    return customer


# =========================================================
# TRANSACTION HISTORY
# =========================================================

def get_transaction_history(customer_id):

    conn = get_db()

    batches = conn.execute("""
        SELECT
            id,
            customer_id,
            total_amount,
            created_at

        FROM transaction_batches

        WHERE customer_id = ?

        ORDER BY id DESC
    """, (customer_id,)).fetchall()

    history = []

    for batch in batches:

        transactions = conn.execute("""
            SELECT
                id,
                batch_id,
                customer_id,
                amount,
                transaction_datetime,
                utr_number,
                site

            FROM transactions

            WHERE batch_id = ?

            ORDER BY id ASC
        """, (batch["id"],)).fetchall()

        refunds = conn.execute("""
            SELECT
                id,
                batch_id,
                customer_id,
                amount,
                transaction_datetime,
                remarks,
                created_at

            FROM refunds

            WHERE batch_id = ?

            ORDER BY id DESC
        """, (batch["id"],)).fetchall()

        total_refund = sum(
            float(r["amount"] or 0)
            for r in refunds
        )

        history.append({

            "id": batch["id"],

            "customer_id": batch["customer_id"],

            "total_amount": batch["total_amount"],

            "created_at": batch["created_at"],

            "transactions": transactions,

            "refunds": refunds,

            "total_refund": total_refund
        })

    conn.close()

    return history


# =========================================================
# TOTAL TRANSACTION AMOUNT
# =========================================================

def get_total_transaction_amount(customer_id):

    conn = get_db()

    result = conn.execute("""
        SELECT
            COALESCE(SUM(amount), 0) AS total

        FROM transactions

        WHERE customer_id = ?
    """, (customer_id,)).fetchone()

    conn.close()

    return float(result["total"] or 0)


# =========================================================
# CAPITAL HISTORY
# =========================================================

def get_capital_history():

    conn = get_db()

    rows = conn.execute("""
        SELECT
            id,
            amount,
            transaction_datetime,
            site,
            payment_type,
            remarks,
            created_at

        FROM capital_history

        ORDER BY id DESC
    """).fetchall()

    conn.close()

    return rows


# =========================================================
# RECHARGES (Mobile Recharge)
# =========================================================

def get_all_recharges():

    conn = get_db()

    rows = conn.execute("""
        SELECT
            recharges.id,
            recharges.mobile_number,
            recharges.operator,
            recharges.circle,
            recharges.amount,
            recharges.transaction_datetime,
            recharges.remarks,
            recharges.created_at,

            COALESCE((
                SELECT SUM(recharge_refunds.amount)
                FROM recharge_refunds
                WHERE recharge_refunds.recharge_id = recharges.id
            ), 0) AS total_refund

        FROM recharges

        ORDER BY recharges.id DESC
    """).fetchall()

    conn.close()

    return rows


def get_today_recharge_amount():

    today = datetime.now().strftime("%Y-%m-%d")

    conn = get_db()

    rows = conn.execute("""
        SELECT
            amount,
            transaction_datetime

        FROM recharges
    """).fetchall()

    conn.close()

    total = 0.0

    for row in rows:

        row_date = normalize_transaction_date(
            row["transaction_datetime"]
        )

        if row_date == today:

            total += float(
                row["amount"] or 0
            )

    return total


# =========================================================
# REFUNDS
# =========================================================

def get_refunds_for_batch(batch_id):

    conn = get_db()

    rows = conn.execute("""
        SELECT
            id,
            batch_id,
            customer_id,
            amount,
            transaction_datetime,
            remarks,
            created_at

        FROM refunds

        WHERE batch_id = ?

        ORDER BY id DESC
    """, (batch_id,)).fetchall()

    conn.close()

    return rows


def get_total_refund_for_batch(batch_id):

    conn = get_db()

    result = conn.execute("""
        SELECT
            COALESCE(SUM(amount), 0) AS total

        FROM refunds

        WHERE batch_id = ?
    """, (batch_id,)).fetchone()

    conn.close()

    return float(result["total"] or 0)


# =========================================================
# RECHARGE REFUNDS
# =========================================================

def get_refunds_for_recharge(recharge_id):

    conn = get_db()

    rows = conn.execute("""
        SELECT
            id,
            recharge_id,
            amount,
            transaction_datetime,
            remarks,
            created_at

        FROM recharge_refunds

        WHERE recharge_id = ?

        ORDER BY id DESC
    """, (recharge_id,)).fetchall()

    conn.close()

    return rows


def get_total_refund_for_recharge(recharge_id):

    conn = get_db()

    result = conn.execute("""
        SELECT
            COALESCE(SUM(amount), 0) AS total

        FROM recharge_refunds

        WHERE recharge_id = ?
    """, (recharge_id,)).fetchone()

    conn.close()

    return float(result["total"] or 0)


# =========================================================
# LOGIN REQUIRED DECORATOR
# =========================================================

def login_required(view_function):

    @wraps(view_function)
    def wrapped_view(*args, **kwargs):

        if not session.get("user_id"):

            return redirect(
                url_for(
                    "login",
                    next=request.path
                )
            )

        return view_function(*args, **kwargs)

    return wrapped_view


# =========================================================
# LOGIN
# =========================================================

@app.route(
    "/login",
    methods=["GET", "POST"]
)
def login():

    if session.get("user_id"):

        return redirect(
            url_for("home")
        )

    error = None

    if request.method == "POST":

        username = request.form.get(
            "username",
            ""
        ).strip()

        password = request.form.get(
            "password",
            ""
        )

        conn = get_db()

        user = conn.execute("""
            SELECT
                id,
                username,
                password_hash

            FROM users

            WHERE username = ?
        """, (username,)).fetchone()

        conn.close()

        if user and check_password_hash(
            user["password_hash"],
            password
        ):

            session.clear()

            session["user_id"] = user["id"]
            session["username"] = user["username"]

            next_page = request.form.get(
                "next",
                ""
            ).strip()

            if next_page and next_page.startswith("/"):

                return redirect(next_page)

            return redirect(
                url_for("home")
            )

        error = "Invalid username or password."

    next_page = request.args.get(
        "next",
        ""
    )

    return render_template(

        "login.html",

        error=error,

        next_page=next_page
    )


# =========================================================
# LOGOUT
# =========================================================

@app.route("/logout")
def logout():

    session.clear()

    return redirect(
        url_for("login")
    )


# =========================================================
# CHANGE PASSWORD
# =========================================================

@app.route(
    "/change-password",
    methods=["GET", "POST"]
)
@login_required
def change_password():

    error = None
    success = None

    if request.method == "POST":

        current_password = request.form.get(
            "current_password",
            ""
        )

        new_password = request.form.get(
            "new_password",
            ""
        )

        confirm_password = request.form.get(
            "confirm_password",
            ""
        )

        conn = get_db()

        user = conn.execute("""
            SELECT
                id,
                password_hash

            FROM users

            WHERE id = ?
        """, (session["user_id"],)).fetchone()

        if not user or not check_password_hash(
            user["password_hash"],
            current_password
        ):

            error = "Current password is incorrect."

        elif len(new_password) < 6:

            error = (
                "New password must be at least "
                "6 characters long."
            )

        elif new_password != confirm_password:

            error = "New passwords do not match."

        else:

            new_hash = generate_password_hash(
                new_password
            )

            conn.execute("""
                UPDATE users

                SET password_hash = ?

                WHERE id = ?
            """, (
                new_hash,
                session["user_id"]
            ))

            conn.commit()

            success = "Password updated successfully."

        conn.close()

    return render_template(

        "change_password.html",

        error=error,

        success=success
    )


# =========================================================
# HOME
# =========================================================

@app.route("/")
@login_required
def home():

    customers = get_all_customers()

    total_accounts = len(customers)

    capital = get_capital()

    today = datetime.now().strftime("%Y-%m-%d")

    today_transactions = get_transactions_by_date(
        today
    )

    today_transaction_amount = sum(
        float(row["amount"] or 0)
        for row in today_transactions
    )

    today_recharge_amount = get_today_recharge_amount()

    return render_template(

        "index.html",

        customers=customers,

        customer=None,

        search_account="",

        search_name="",

        search_mobile="",

        total_accounts=total_accounts,

        capital=capital,

        today_transaction_amount=today_transaction_amount,

        today_recharge_amount=today_recharge_amount
    )


# =========================================================
# CAPITAL PAGE
# =========================================================

@app.route(
    "/capital",
    methods=["GET", "POST"]
)
@login_required
def capital_page():

    if request.method == "POST":

        amount_text = request.form.get(
            "capital",
            ""
        ).strip()

        try:

            amount = float(
                amount_text or 0
            )

            if amount < 0:
                amount = 0

        except ValueError:

            return (
                "Invalid Capital Amount. "
                "<a href='/capital'>Back</a>"
            )

        set_capital(amount)

        return redirect(
            url_for("capital_page")
        )

    capital = get_capital()

    capital_history = get_capital_history()

    return render_template(

        "capital.html",

        capital=capital,

        capital_history=capital_history,

        edit_history=None
    )


# =========================================================
# ADD CAPITAL
# =========================================================

@app.route(
    "/add-capital",
    methods=["POST"]
)
@login_required
def add_capital():

    # -----------------------------------------------------
    # AMOUNT
    # -----------------------------------------------------

    amount_text = request.form.get(
        "add_capital",
        ""
    ).strip()

    # -----------------------------------------------------
    # MANUAL DATE & TIME
    # -----------------------------------------------------

    transaction_datetime = request.form.get(
        "transaction_datetime",
        ""
    ).strip()

    # -----------------------------------------------------
    # SITE
    # -----------------------------------------------------

    site = request.form.get(
        "site",
        ""
    ).strip()

    # -----------------------------------------------------
    # PAYMENT TYPE
    # -----------------------------------------------------

    payment_type = request.form.get(
        "payment_type",
        ""
    ).strip().upper()

    allowed_payment_types = {
        "PAID BALANCE",
        "CREDIT BALANCE",
        "ADVANCE BALANCE"
    }

    if payment_type not in allowed_payment_types:

        return (
            "Invalid Payment Type. "
            "Please select a valid Payment Type. "
            "<br><a href='/capital'>Back</a>"
        )

    # -----------------------------------------------------
    # REMARKS
    # -----------------------------------------------------

    remarks = request.form.get(
        "remarks",
        ""
    ).strip()

    # -----------------------------------------------------
    # VALIDATE AMOUNT
    # -----------------------------------------------------

    try:

        amount = float(
            amount_text or 0
        )

    except ValueError:

        return (
            "Invalid Capital Amount. "
            "<a href='/capital'>Back</a>"
        )

    if amount <= 0:

        return (
            "Amount must be greater than 0. "
            "<a href='/capital'>Back</a>"
        )

    # -----------------------------------------------------
    # MANUAL DATE & TIME PROCESSING
    # -----------------------------------------------------

    if not transaction_datetime:

        transaction_datetime = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    else:

        transaction_datetime = transaction_datetime.strip()

        transaction_datetime = transaction_datetime.replace(
            "T",
            " "
        )

        parsed = None

        formats = [

            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",

            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y %H:%M",

            "%d-%m-%Y %H:%M:%S",
            "%d-%m-%Y %H:%M",

            "%d.%m.%Y %H:%M:%S",
            "%d.%m.%Y %H:%M",

            "%Y-%m-%d %I:%M:%S %p",
            "%Y-%m-%d %I:%M %p",

            "%d/%m/%Y %I:%M:%S %p",
            "%d/%m/%Y %I:%M %p",

            "%d-%m-%Y %I:%M:%S %p",
            "%d-%m-%Y %I:%M %p",

            "%d.%m.%Y %I:%M:%S %p",
            "%d.%m.%Y %I:%M %p"
        ]

        for fmt in formats:

            try:

                parsed = datetime.strptime(
                    transaction_datetime,
                    fmt
                )

                break

            except ValueError:
                pass

        if parsed is None:

            return (
                "Invalid Date & Time. "
                "Example: 28/08/2026 07:30 PM "
                "<br><a href='/capital'>Back</a>"
            )

        transaction_datetime = parsed.strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    # -----------------------------------------------------
    # SAVE CAPITAL
    # -----------------------------------------------------

    conn = get_db()

    try:

        conn.execute("""
            UPDATE capital

            SET amount = amount + ?

            WHERE id = 1
        """, (amount,))

        conn.execute("""
            INSERT INTO capital_history
            (
                amount,
                transaction_datetime,
                site,
                payment_type,
                remarks
            )

            VALUES (?, ?, ?, ?, ?)
        """, (
            amount,
            transaction_datetime,
            site,
            payment_type,
            remarks
        ))

        conn.commit()

    except Exception as e:

        conn.rollback()
        conn.close()

        return (
            "Capital save karte time error aa gaya. "
            "<br>"
            f"{e}"
            "<br>"
            "<a href='/capital'>Back</a>"
        )

    conn.close()

    return redirect(
        url_for("capital_page")
    )


# =========================================================
# EDIT CAPITAL HISTORY
# =========================================================

@app.route(
    "/edit-capital/<int:history_id>",
    methods=["GET", "POST"]
)
@login_required
def edit_capital(history_id):

    conn = get_db()

    history = conn.execute("""
        SELECT *
        FROM capital_history

        WHERE id = ?
    """, (history_id,)).fetchone()

    if history is None:

        conn.close()

        return "Capital history not found."

    # -----------------------------------------------------
    # GET
    # -----------------------------------------------------

    if request.method == "GET":

        conn.close()

        return render_template(

            "capital.html",

            capital=get_capital(),

            capital_history=get_capital_history(),

            edit_history=history
        )

    # -----------------------------------------------------
    # AMOUNT
    # -----------------------------------------------------

    amount_text = request.form.get(
        "amount",
        ""
    ).strip()

    # -----------------------------------------------------
    # DATE & TIME
    # -----------------------------------------------------

    transaction_datetime = request.form.get(
        "transaction_datetime",
        ""
    ).strip()

    # -----------------------------------------------------
    # SITE
    # -----------------------------------------------------

    site = request.form.get(
        "site",
        ""
    ).strip()

    # -----------------------------------------------------
    # PAYMENT TYPE
    # -----------------------------------------------------

    payment_type = request.form.get(
        "payment_type",
        ""
    ).strip().upper()

    allowed_payment_types = {
        "PAID BALANCE",
        "CREDIT BALANCE",
        "ADVANCE BALANCE"
    }

    if payment_type not in allowed_payment_types:

        conn.close()

        return (
            "Invalid Payment Type. "
            "Please select a valid Payment Type. "
            "<br><a href='/capital'>Back</a>"
        )

    # -----------------------------------------------------
    # REMARKS
    # -----------------------------------------------------

    remarks = request.form.get(
        "remarks",
        ""
    ).strip()

    # -----------------------------------------------------
    # VALIDATE AMOUNT
    # -----------------------------------------------------

    try:

        new_amount = float(
            amount_text or 0
        )

    except ValueError:

        conn.close()

        return (
            "Invalid Capital Amount. "
            "<a href='/capital'>Back</a>"
        )

    if new_amount <= 0:

        conn.close()

        return (
            "Amount must be greater than 0. "
            "<a href='/capital'>Back</a>"
        )

    # -----------------------------------------------------
    # DATE & TIME
    # -----------------------------------------------------

    if not transaction_datetime:

        conn.close()

        return (
            "Date & Time required hai. "
            "<a href='/capital'>Back</a>"
        )

    transaction_datetime = transaction_datetime.replace(
        "T",
        " "
    )

    parsed = None

    formats = [

        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",

        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",

        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",

        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y %H:%M",

        "%Y-%m-%d %I:%M:%S %p",
        "%Y-%m-%d %I:%M %p",

        "%d/%m/%Y %I:%M:%S %p",
        "%d/%m/%Y %I:%M %p",

        "%d-%m-%Y %I:%M:%S %p",
        "%d-%m-%Y %I:%M %p",

        "%d.%m.%Y %I:%M:%S %p",
        "%d.%m.%Y %I:%M %p"
    ]

    for fmt in formats:

        try:

            parsed = datetime.strptime(
                transaction_datetime,
                fmt
            )

            break

        except ValueError:
            pass

    if parsed is None:

        conn.close()

        return (
            "Invalid Date & Time. "
            "Example: 28/08/2026 07:30 PM "
            "<br><a href='/capital'>Back</a>"
        )

    transaction_datetime = parsed.strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    # -----------------------------------------------------
    # OLD AMOUNT
    # -----------------------------------------------------

    old_amount = float(
        history["amount"] or 0
    )

    difference = new_amount - old_amount

    # -----------------------------------------------------
    # CURRENT CAPITAL
    # -----------------------------------------------------

    capital_row = conn.execute("""
        SELECT amount
        FROM capital

        WHERE id = 1
    """).fetchone()

    current_capital = float(
        capital_row["amount"] or 0
    )

    # अगर amount बढ़ रहा है
    if difference > 0:

        if difference > current_capital:

            conn.close()

            return (
                f"Update nahi hua. "
                f"Current Capital ₹{current_capital:.2f} hai, "
                f"aur ₹{difference:.2f} extra chahiye. "
                f"<br><a href='/capital'>Back</a>"
            )

        new_capital = current_capital - difference

    # अगर amount कम हो रहा है
    elif difference < 0:

        new_capital = current_capital + abs(
            difference
        )

    else:

        new_capital = current_capital

    if new_capital < 0:

        conn.close()

        return (
            "Update ke baad capital negative ho jayega. "
            "<a href='/capital'>Back</a>"
        )

    # -----------------------------------------------------
    # UPDATE
    # -----------------------------------------------------

    try:

        conn.execute("""
            UPDATE capital

            SET amount = ?

            WHERE id = 1
        """, (new_capital,))

        conn.execute("""
            UPDATE capital_history

            SET
                amount = ?,
                transaction_datetime = ?,
                site = ?,
                payment_type = ?,
                remarks = ?

            WHERE id = ?
        """, (
            new_amount,
            transaction_datetime,
            site,
            payment_type,
            remarks,
            history_id
        ))

        conn.commit()

    except Exception as e:

        conn.rollback()
        conn.close()

        return (
            "Capital history update karte time "
            "error aa gaya."
            "<br>"
            f"{e}"
            "<br>"
            "<a href='/capital'>Back</a>"
        )

    conn.close()

    return redirect(
        url_for("capital_page")
    )


# =========================================================
# DELETE CAPITAL HISTORY
# =========================================================

@app.route(
    "/delete-capital/<int:history_id>",
    methods=["POST"]
)
@login_required
def delete_capital(history_id):

    conn = get_db()

    history = conn.execute(
        """
        SELECT amount

        FROM capital_history

        WHERE id = ?
        """,
        (history_id,)
    ).fetchone()

    if history is None:

        conn.close()

        return redirect(
            url_for("capital_page")
        )

    amount = float(
        history["amount"] or 0
    )

    capital_row = conn.execute(
        """
        SELECT amount

        FROM capital

        WHERE id = 1
        """
    ).fetchone()

    current_capital = float(
        capital_row["amount"] or 0
    )

    if amount > current_capital:

        conn.close()

        return (
            f"Delete nahi hua. "
            f"Current Capital ₹{current_capital:.2f} hai, "
            f"lekin history amount ₹{amount:.2f} hai. "
            f"<br><a href='/capital'>Back</a>"
        )

    try:

        conn.execute(
            """
            DELETE FROM capital_history

            WHERE id = ?
            """,
            (history_id,)
        )

        conn.execute(
            """
            UPDATE capital

            SET amount = amount - ?

            WHERE id = 1
            """,
            (amount,)
        )

        conn.commit()

    except Exception:

        conn.rollback()
        conn.close()

        return (
            "Capital history delete karte time "
            "error aa gaya. "
            "<a href='/capital'>Back</a>"
        )

    conn.close()

    return redirect(
        url_for("capital_page")
    )


# =========================================================
# RECHARGE PAGE
# =========================================================

def get_enriched_recharges():

    recharges = get_all_recharges()

    enriched_recharges = []

    for row in recharges:

        row_dict = dict(row)

        row_dict["refunds"] = get_refunds_for_recharge(
            row["id"]
        )

        enriched_recharges.append(row_dict)

    return enriched_recharges


@app.route("/recharge")
@login_required
def recharge_page():

    capital = get_capital()

    return render_template(

        "recharge.html",

        capital=capital,

        recharges=get_enriched_recharges(),

        edit_recharge=None
    )


# =========================================================
# ADD RECHARGE
# =========================================================

@app.route(
    "/add-recharge",
    methods=["POST"]
)
@login_required
def add_recharge():

    mobile_number = request.form.get(
        "mobile_number",
        ""
    ).strip()

    operator = request.form.get(
        "operator",
        ""
    ).strip()

    circle = request.form.get(
        "circle",
        ""
    ).strip()

    amount_text = request.form.get(
        "amount",
        ""
    ).strip()

    transaction_datetime = request.form.get(
        "transaction_datetime",
        ""
    ).strip()

    remarks = request.form.get(
        "remarks",
        ""
    ).strip()

    if not mobile_number:

        return (
            "Mobile Number required hai. "
            "<a href='/recharge'>Back</a>"
        )

    try:

        amount = float(
            amount_text or 0
        )

    except ValueError:

        return (
            "Invalid Amount. "
            "<a href='/recharge'>Back</a>"
        )

    if amount <= 0:

        return (
            "Amount must be greater than 0. "
            "<a href='/recharge'>Back</a>"
        )

    # -----------------------------------------------------
    # DATE & TIME
    # -----------------------------------------------------

    if not transaction_datetime:

        transaction_datetime = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    else:

        transaction_datetime = transaction_datetime.strip()

        transaction_datetime = transaction_datetime.replace(
            "T",
            " "
        )

        parsed = None

        formats = [

            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",

            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y %H:%M",

            "%d-%m-%Y %H:%M:%S",
            "%d-%m-%Y %H:%M",

            "%d.%m.%Y %H:%M:%S",
            "%d.%m.%Y %H:%M",

            "%Y-%m-%d %I:%M:%S %p",
            "%Y-%m-%d %I:%M %p",

            "%d/%m/%Y %I:%M:%S %p",
            "%d/%m/%Y %I:%M %p",

            "%d-%m-%Y %I:%M:%S %p",
            "%d-%m-%Y %I:%M %p",

            "%d.%m.%Y %I:%M:%S %p",
            "%d.%m.%Y %I:%M %p"
        ]

        for fmt in formats:

            try:

                parsed = datetime.strptime(
                    transaction_datetime,
                    fmt
                )

                break

            except ValueError:
                pass

        if parsed is None:

            return (
                "Invalid Date & Time. "
                "Example: 28/08/2026 07:30 PM "
                "<br><a href='/recharge'>Back</a>"
            )

        transaction_datetime = parsed.strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    # -----------------------------------------------------
    # CAPITAL CHECK
    # -----------------------------------------------------

    current_capital = get_capital()

    if amount > current_capital:

        return (
            f"Recharge nahi hua. "
            f"Capital ₹{current_capital:.2f} hai, "
            f"lekin recharge ₹{amount:.2f} ka hai. "
            f"<br><a href='/recharge'>Back</a>"
        )

    # -----------------------------------------------------
    # SAVE RECHARGE
    # -----------------------------------------------------

    conn = get_db()

    try:

        conn.execute("""
            UPDATE capital

            SET amount = amount - ?

            WHERE id = 1
        """, (amount,))

        conn.execute("""
            INSERT INTO recharges
            (
                mobile_number,
                operator,
                circle,
                amount,
                transaction_datetime,
                remarks
            )

            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            mobile_number,
            operator,
            circle,
            amount,
            transaction_datetime,
            remarks
        ))

        conn.commit()

    except Exception as e:

        conn.rollback()
        conn.close()

        return (
            "Recharge save karte time error aa gaya. "
            "<br>"
            f"{e}"
            "<br>"
            "<a href='/recharge'>Back</a>"
        )

    conn.close()

    return redirect(
        url_for("recharge_page")
    )


# =========================================================
# EDIT RECHARGE
# =========================================================

@app.route(
    "/edit-recharge/<int:recharge_id>",
    methods=["GET", "POST"]
)
@login_required
def edit_recharge(recharge_id):

    conn = get_db()

    recharge = conn.execute("""
        SELECT *
        FROM recharges

        WHERE id = ?
    """, (recharge_id,)).fetchone()

    if recharge is None:

        conn.close()

        return "Recharge record not found."

    # -----------------------------------------------------
    # GET
    # -----------------------------------------------------

    if request.method == "GET":

        conn.close()

        return render_template(

            "recharge.html",

            capital=get_capital(),

            recharges=get_enriched_recharges(),

            edit_recharge=recharge
        )

    # -----------------------------------------------------
    # FORM DATA
    # -----------------------------------------------------

    mobile_number = request.form.get(
        "mobile_number",
        ""
    ).strip()

    operator = request.form.get(
        "operator",
        ""
    ).strip()

    circle = request.form.get(
        "circle",
        ""
    ).strip()

    amount_text = request.form.get(
        "amount",
        ""
    ).strip()

    transaction_datetime = request.form.get(
        "transaction_datetime",
        ""
    ).strip()

    remarks = request.form.get(
        "remarks",
        ""
    ).strip()

    if not mobile_number:

        conn.close()

        return (
            "Mobile Number required hai. "
            "<a href='/recharge'>Back</a>"
        )

    try:

        new_amount = float(
            amount_text or 0
        )

    except ValueError:

        conn.close()

        return (
            "Invalid Amount. "
            "<a href='/recharge'>Back</a>"
        )

    if new_amount <= 0:

        conn.close()

        return (
            "Amount must be greater than 0. "
            "<a href='/recharge'>Back</a>"
        )

    if not transaction_datetime:

        conn.close()

        return (
            "Date & Time required hai. "
            "<a href='/recharge'>Back</a>"
        )

    transaction_datetime = transaction_datetime.replace(
        "T",
        " "
    )

    parsed = None

    formats = [

        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",

        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",

        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",

        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y %H:%M",

        "%Y-%m-%d %I:%M:%S %p",
        "%Y-%m-%d %I:%M %p",

        "%d/%m/%Y %I:%M:%S %p",
        "%d/%m/%Y %I:%M %p",

        "%d-%m-%Y %I:%M:%S %p",
        "%d-%m-%Y %I:%M %p",

        "%d.%m.%Y %I:%M:%S %p",
        "%d.%m.%Y %I:%M %p"
    ]

    for fmt in formats:

        try:

            parsed = datetime.strptime(
                transaction_datetime,
                fmt
            )

            break

        except ValueError:
            pass

    if parsed is None:

        conn.close()

        return (
            "Invalid Date & Time. "
            "Example: 28/08/2026 07:30 PM "
            "<br><a href='/recharge'>Back</a>"
        )

    transaction_datetime = parsed.strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    # -----------------------------------------------------
    # CAPITAL ADJUSTMENT (difference based)
    # -----------------------------------------------------

    old_amount = float(
        recharge["amount"] or 0
    )

    difference = new_amount - old_amount

    capital_row = conn.execute("""
        SELECT amount
        FROM capital

        WHERE id = 1
    """).fetchone()

    current_capital = float(
        capital_row["amount"] or 0
    )

    if difference > 0:

        if difference > current_capital:

            conn.close()

            return (
                f"Update nahi hua. "
                f"Current Capital ₹{current_capital:.2f} hai, "
                f"aur ₹{difference:.2f} extra chahiye. "
                f"<br><a href='/recharge'>Back</a>"
            )

        new_capital = current_capital - difference

    elif difference < 0:

        new_capital = current_capital + abs(
            difference
        )

    else:

        new_capital = current_capital

    if new_capital < 0:

        conn.close()

        return (
            "Update ke baad capital negative ho jayega. "
            "<a href='/recharge'>Back</a>"
        )

    # -----------------------------------------------------
    # UPDATE
    # -----------------------------------------------------

    try:

        conn.execute("""
            UPDATE capital

            SET amount = ?

            WHERE id = 1
        """, (new_capital,))

        conn.execute("""
            UPDATE recharges

            SET
                mobile_number = ?,
                operator = ?,
                circle = ?,
                amount = ?,
                transaction_datetime = ?,
                remarks = ?

            WHERE id = ?
        """, (
            mobile_number,
            operator,
            circle,
            new_amount,
            transaction_datetime,
            remarks,
            recharge_id
        ))

        conn.commit()

    except Exception as e:

        conn.rollback()
        conn.close()

        return (
            "Recharge update karte time "
            "error aa gaya."
            "<br>"
            f"{e}"
            "<br>"
            "<a href='/recharge'>Back</a>"
        )

    conn.close()

    return redirect(
        url_for("recharge_page")
    )


# =========================================================
# DELETE RECHARGE
# =========================================================

@app.route(
    "/delete-recharge/<int:recharge_id>",
    methods=["POST"]
)
@login_required
def delete_recharge(recharge_id):

    conn = get_db()

    recharge = conn.execute("""
        SELECT amount

        FROM recharges

        WHERE id = ?
    """, (recharge_id,)).fetchone()

    if recharge is None:

        conn.close()

        return redirect(
            url_for("recharge_page")
        )

    amount = float(
        recharge["amount"] or 0
    )

    # Agar is recharge pe pehle se refund ho chuka hai,
    # to wo amount capital mein already wapas aa chuka
    # hai — isliye sirf बाकी hi wapas jodenge.

    already_refunded = get_total_refund_for_recharge(
        recharge_id
    )

    refundable_amount = amount - already_refunded

    if refundable_amount < 0:
        refundable_amount = 0

    try:

        conn.execute("""
            DELETE FROM recharge_refunds

            WHERE recharge_id = ?
        """, (recharge_id,))

        conn.execute("""
            DELETE FROM recharges

            WHERE id = ?
        """, (recharge_id,))

        conn.execute("""
            UPDATE capital

            SET amount = amount + ?

            WHERE id = 1
        """, (refundable_amount,))

        conn.commit()

    except Exception:

        conn.rollback()
        conn.close()

        return (
            "Recharge delete karte time "
            "error aa gaya. "
            "<a href='/recharge'>Back</a>"
        )

    conn.close()

    return redirect(
        url_for("recharge_page")
    )


# =========================================================
# ADD RECHARGE REFUND
# =========================================================

@app.route(
    "/add-recharge-refund/<int:recharge_id>",
    methods=["POST"]
)
@login_required
def add_recharge_refund(recharge_id):

    conn = get_db()

    recharge = conn.execute("""
        SELECT
            id,
            amount

        FROM recharges

        WHERE id = ?
    """, (recharge_id,)).fetchone()

    if recharge is None:

        conn.close()

        return "Recharge record not found."

    amount_text = request.form.get(
        "refund_amount",
        ""
    ).strip()

    transaction_datetime = request.form.get(
        "refund_datetime",
        ""
    ).strip()

    remarks = request.form.get(
        "refund_remarks",
        ""
    ).strip()

    try:

        amount = float(
            amount_text or 0
        )

    except ValueError:

        conn.close()

        return (
            "Invalid Refund Amount. "
            "<a href='/recharge'>Back</a>"
        )

    if amount <= 0:

        conn.close()

        return (
            "Refund Amount must be greater than 0. "
            "<a href='/recharge'>Back</a>"
        )

    # -----------------------------------------------------
    # REFUND, RECHARGE KE AMOUNT SE ZYADA NAHI HO SAKTA
    # -----------------------------------------------------

    recharge_amount = float(
        recharge["amount"] or 0
    )

    already_refunded = get_total_refund_for_recharge(
        recharge_id
    )

    remaining_refundable = (
        recharge_amount - already_refunded
    )

    if amount > remaining_refundable:

        conn.close()

        return (
            f"Refund nahi hua. Is recharge ka "
            f"amount ₹{recharge_amount:.2f} hai, "
            f"pehle se ₹{already_refunded:.2f} refund "
            f"ho chuka hai. Sirf ₹{remaining_refundable:.2f} "
            f"tak hi refund kiya ja sakta hai. "
            f"<br><a href='/recharge'>Back</a>"
        )

    # -----------------------------------------------------
    # DATE & TIME
    # -----------------------------------------------------

    if not transaction_datetime:

        transaction_datetime = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    else:

        transaction_datetime = transaction_datetime.replace(
            "T",
            " "
        )

        parsed = None

        formats = [

            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",

            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y %H:%M",

            "%d-%m-%Y %H:%M:%S",
            "%d-%m-%Y %H:%M",

            "%d.%m.%Y %H:%M:%S",
            "%d.%m.%Y %H:%M"
        ]

        for fmt in formats:

            try:

                parsed = datetime.strptime(
                    transaction_datetime,
                    fmt
                )

                break

            except ValueError:
                pass

        if parsed is None:

            conn.close()

            return (
                "Invalid Date & Time. "
                "Example: 28/08/2026 07:30 PM "
                "<br><a href='/recharge'>Back</a>"
            )

        transaction_datetime = parsed.strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    # -----------------------------------------------------
    # SAVE REFUND
    # -----------------------------------------------------

    try:

        conn.execute("""
            INSERT INTO recharge_refunds
            (
                recharge_id,
                amount,
                transaction_datetime,
                remarks
            )

            VALUES (?, ?, ?, ?)
        """, (
            recharge_id,
            amount,
            transaction_datetime,
            remarks
        ))

        conn.execute("""
            UPDATE capital

            SET amount = amount + ?

            WHERE id = 1
        """, (amount,))

        conn.commit()

    except Exception as e:

        conn.rollback()
        conn.close()

        return (
            "Refund save karte time error aa gaya. "
            f"<br>{e}"
            "<br><a href='/recharge'>Back</a>"
        )

    conn.close()

    return redirect(
        url_for("recharge_page")
    )


# =========================================================
# DELETE RECHARGE REFUND
# =========================================================

@app.route(
    "/delete-recharge-refund/<int:refund_id>",
    methods=["POST"]
)
@login_required
def delete_recharge_refund(refund_id):

    conn = get_db()

    refund = conn.execute("""
        SELECT
            id,
            amount

        FROM recharge_refunds

        WHERE id = ?
    """, (refund_id,)).fetchone()

    if refund is None:

        conn.close()

        return redirect(
            url_for("recharge_page")
        )

    amount = float(
        refund["amount"] or 0
    )

    capital_row = conn.execute("""
        SELECT amount

        FROM capital

        WHERE id = 1
    """).fetchone()

    current_capital = float(
        capital_row["amount"] or 0
    )

    if amount > current_capital:

        conn.close()

        return (
            f"Refund delete nahi hua. "
            f"Current Capital ₹{current_capital:.2f} hai, "
            f"lekin refund amount ₹{amount:.2f} hai. "
            f"<br><a href='/recharge'>Back</a>"
        )

    try:

        conn.execute("""
            DELETE FROM recharge_refunds

            WHERE id = ?
        """, (refund_id,))

        conn.execute("""
            UPDATE capital

            SET amount = amount - ?

            WHERE id = 1
        """, (amount,))

        conn.commit()

    except Exception:

        conn.rollback()
        conn.close()

        return (
            "Refund delete karte time error aa gaya. "
            "<a href='/recharge'>Back</a>"
        )

    conn.close()

    return redirect(
        url_for("recharge_page")
    )


# =========================================================
# CAPITAL ALIAS
# =========================================================

@app.route(
    "/set-capital",
    methods=["GET", "POST"]
)
@login_required
def set_capital_route():

    if request.method == "GET":

        return redirect(
            url_for("capital_page")
        )

    amount_text = request.form.get(
        "capital",
        ""
    ).strip()

    try:

        amount = float(
            amount_text or 0
        )

        if amount < 0:
            amount = 0

    except ValueError:

        return """
        <h2 style="font-family:Arial;color:red;">
            Invalid Capital Amount
        </h2>

        <a href="/capital">Back</a>
        """

    set_capital(amount)

    return redirect(
        url_for("capital_page")
    )


# =========================================================
# ADD ACCOUNT
# =========================================================

@app.route(
    "/add-account",
    methods=["POST"]
)
@login_required
def add_account():

    account_number = request.form.get(
        "account_number",
        ""
    ).strip()

    name = request.form.get(
        "name",
        ""
    ).strip()

    mobile = request.form.get(
        "mobile",
        ""
    ).strip()

    bank_name = request.form.get(
        "bank_name",
        ""
    ).strip()

    ifsc_code = request.form.get(
        "ifsc_code",
        ""
    ).strip().upper()

    if not account_number or not name:

        flash(
            "Account Number and Name are required.",
            "error"
        )

        return redirect(
            url_for("home")
        )

    if not is_valid_mobile_number(mobile):

        flash(
            "Mobile Number invalid hai. "
            "10 digit ka valid Indian number "
            "(6-9 se start) dalein, ya khaali chodein.",
            "error"
        )

        return redirect(
            url_for("home")
        )

    if not is_valid_ifsc_code(ifsc_code):

        flash(
            "IFSC Code invalid hai. Format: "
            "4 letters + 0 + 6 alphanumeric "
            "(jaise SBIN0001234), ya khaali chodein.",
            "error"
        )

        return redirect(
            url_for("home")
        )

    conn = get_db()

    try:

        conn.execute("""
            INSERT INTO customers
            (
                account_number,
                name,
                mobile,
                bank_name,
                ifsc_code
            )

            VALUES (?, ?, ?, ?, ?)
        """, (
            account_number,
            name,
            mobile,
            bank_name,
            ifsc_code
        ))

        conn.commit()

    except sqlite3.IntegrityError:

        conn.close()

        flash(
            "This Account Number already exists.",
            "error"
        )

        return redirect(
            url_for("home")
        )

    conn.close()

    flash(
        "Account added successfully.",
        "success"
    )

    return redirect(
        url_for("home")
    )


# =========================================================
# SEARCH
# =========================================================

@app.route(
    "/search",
    methods=["GET", "POST"]
)
@login_required
def search():

    account_number = request.values.get(
        "account_number",
        ""
    ).strip()

    name = request.values.get(
        "name",
        ""
    ).strip()

    mobile = request.values.get(
        "mobile",
        ""
    ).strip()

    conn = get_db()

    customers = []

    customer = None

    if account_number:

        customer = conn.execute("""
            SELECT *
            FROM customers

            WHERE account_number = ?
        """, (account_number,)).fetchone()

        if customer:
            customers = [customer]

    elif name:

        customers = conn.execute("""
            SELECT *
            FROM customers

            WHERE name LIKE ?

            ORDER BY name
        """, (
            f"%{name}%",
        )).fetchall()

    elif mobile:

        customers = conn.execute("""
            SELECT *
            FROM customers

            WHERE mobile LIKE ?

            ORDER BY name
        """, (
            f"%{mobile}%",
        )).fetchall()

    else:

        customers = conn.execute("""
            SELECT *
            FROM customers

            ORDER BY id DESC
        """).fetchall()

    conn.close()

    total_accounts = len(
        get_all_customers()
    )

    capital = get_capital()

    today = datetime.now().strftime(
        "%Y-%m-%d"
    )

    today_transactions = get_transactions_by_date(
        today
    )

    today_transaction_amount = sum(
        float(row["amount"] or 0)
        for row in today_transactions
    )

    today_recharge_amount = get_today_recharge_amount()

    return render_template(

        "index.html",

        customers=customers,

        customer=customer,

        search_account=account_number,

        search_name=name,

        search_mobile=mobile,

        total_accounts=total_accounts,

        capital=capital,

        today_transaction_amount=today_transaction_amount,

        today_recharge_amount=today_recharge_amount
    )


# =========================================================
# TODAY / DATE-WISE TRANSACTIONS
# =========================================================

@app.route("/transactions")
@login_required
def transactions_page():

    selected_date = request.args.get(
        "date",
        ""
    ).strip()

    if not selected_date:

        selected_date = date.today().strftime(
            "%Y-%m-%d"
        )

    transactions = get_transactions_by_date(
        selected_date
    )

    transaction_count = len(
        transactions
    )

    total_amount = sum(
        float(row["amount"] or 0)
        for row in transactions
    )

    return render_template(

        "transactions.html",

        transactions=transactions,

        selected_date=selected_date,

        transaction_count=transaction_count,

        total_transactions=transaction_count,

        total_amount=total_amount,

        report_title="Transaction Report"
    )


# =========================================================
# CUSTOM DATE RANGE TRANSACTION REPORT
# =========================================================

@app.route("/transaction-range-report")
@login_required
def transaction_range_report():

    start_date = request.args.get(
        "start_date",
        ""
    ).strip()

    end_date = request.args.get(
        "end_date",
        ""
    ).strip()

    # -----------------------------------------------------
    # DEFAULT DATE RANGE
    # -----------------------------------------------------

    if not start_date:
        start_date = datetime.now().strftime(
            "%Y-%m-%d"
        )

    if not end_date:
        end_date = datetime.now().strftime(
            "%Y-%m-%d"
        )

    # -----------------------------------------------------
    # VALIDATE DATES
    # -----------------------------------------------------

    normalized_start = normalize_transaction_date(
        start_date
    )

    normalized_end = normalize_transaction_date(
        end_date
    )

    if not normalized_start or not normalized_end:

        return """
        <h2 style="font-family:Arial;color:red;">
            Invalid Date Range
        </h2>

        <p>
            Please select valid From Date and To Date.
        </p>

        <a href="/transactions">
            Back to Transactions
        </a>
        """

    # -----------------------------------------------------
    # START DATE > END DATE
    # -----------------------------------------------------

    if normalized_start > normalized_end:

        return """
        <h2 style="font-family:Arial;color:red;">
            Invalid Date Range
        </h2>

        <p>
            From Date, To Date se badi nahi ho sakti.
        </p>

        <a href="/transactions">
            Back to Transactions
        </a>
        """

    # -----------------------------------------------------
    # GET TRANSACTIONS
    # -----------------------------------------------------

    transactions = get_transactions_by_date_range(
        normalized_start,
        normalized_end
    )

    # -----------------------------------------------------
    # SUMMARY
    # -----------------------------------------------------

    total_transactions = len(
        transactions
    )

    total_amount = sum(
        float(row["amount"] or 0)
        for row in transactions
    )

    # -----------------------------------------------------
    # RENDER
    # -----------------------------------------------------

    return render_template(
        "transactions.html",

        transactions=transactions,

        total_transactions=total_transactions,

        transaction_count=total_transactions,

        total_amount=total_amount,

        selected_date=normalized_start,

        start_date=normalized_start,

        end_date=normalized_end,

        report_title="Custom Date Range Transaction Report",

        custom_range=True
    )


# =========================================================
# PROFILE
# =========================================================

@app.route("/profile/<int:id>")
@login_required
def profile(id):

    customer = get_customer(id)

    if customer is None:

        return "Account not found."

    history = get_transaction_history(id)

    total_amount = get_total_transaction_amount(
        id
    )

    return render_template(

        "profile.html",

        customer=customer,

        history=history,

        total_amount=total_amount,

        print_mode=False,

        edit_mode=False
    )


# =========================================================
# SAVE NEW TRANSACTION BATCH
# =========================================================

@app.route(
    "/save-transactions/<int:id>",
    methods=["POST"]
)
@login_required
def save_transactions(id):

    customer = get_customer(id)

    if customer is None:

        return "Account not found."

    amounts = request.form.getlist(
        "amount[]"
    )

    dates = request.form.getlist(
        "transaction_datetime[]"
    )

    utr_numbers = request.form.getlist(
        "utr_number[]"
    )

    sites = request.form.getlist(
        "site[]"
    )

    rows = []

    total = 0.0

    for index in range(len(amounts)):

        amount_text = (
            amounts[index] or ""
        ).strip()

        date_value = (
            dates[index]
            if index < len(dates)
            else ""
        ).strip()

        utr_value = (
            utr_numbers[index]
            if index < len(utr_numbers)
            else ""
        ).strip()

        site_value = (
            sites[index]
            if index < len(sites)
            else ""
        ).strip()

        if (
            not amount_text
            and not date_value
            and not utr_value
            and not site_value
        ):
            continue

        try:

            amount = float(
                amount_text or 0
            )

        except ValueError:

            amount = 0

        if amount < 0:
            amount = 0

        rows.append((
            amount,
            date_value,
            utr_value,
            site_value
        ))

        total += amount

    if not rows:

        return redirect(
            url_for(
                "profile",
                id=id
            )
        )

    # -----------------------------------------------------
    # CAPITAL CHECK
    # -----------------------------------------------------

    current_capital = get_capital()

    if total > current_capital:

        return (
            f"Transaction save nahi hua. "
            f"Capital ₹{current_capital:.2f} hai, "
            f"lekin transaction ₹{total:.2f} ka hai."
        )

    conn = get_db()

    cursor = conn.cursor()

    created_at = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    try:

        cursor.execute("""
            INSERT INTO transaction_batches
            (
                customer_id,
                total_amount,
                created_at
            )

            VALUES (?, ?, ?)
        """, (
            id,
            total,
            created_at
        ))

        batch_id = cursor.lastrowid

        for (
            amount,
            date_value,
            utr_value,
            site_value
        ) in rows:

            cursor.execute("""
                INSERT INTO transactions
                (
                    batch_id,
                    customer_id,
                    amount,
                    transaction_datetime,
                    utr_number,
                    site
                )

                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                batch_id,
                id,
                amount,
                date_value,
                utr_value,
                site_value
            ))

        # Capital घटाना

        cursor.execute("""
            UPDATE capital

            SET amount = amount - ?

            WHERE id = 1
        """, (total,))

        conn.commit()

    except Exception:

        conn.rollback()
        conn.close()

        return (
            "Transaction save karte time error aa gaya."
        )

    conn.close()

    return redirect(
        url_for(
            "profile",
            id=id
        )
    )


# =========================================================
# EDIT TRANSACTION BATCH
# =========================================================

@app.route(
    "/edit-transaction/<int:batch_id>"
)
@login_required
def edit_transaction(batch_id):

    conn = get_db()

    batch = conn.execute("""
        SELECT *
        FROM transaction_batches

        WHERE id = ?
    """, (batch_id,)).fetchone()

    if batch is None:

        conn.close()

        return (
            "Transaction history not found."
        )

    customer = conn.execute("""
        SELECT *
        FROM customers

        WHERE id = ?
    """, (batch["customer_id"],)).fetchone()

    transactions = conn.execute("""
        SELECT *
        FROM transactions

        WHERE batch_id = ?

        ORDER BY id ASC
    """, (batch_id,)).fetchall()

    conn.close()

    if customer is None:

        return "Customer not found."

    history = get_transaction_history(
        batch["customer_id"]
    )

    total_amount = get_total_transaction_amount(
        batch["customer_id"]
    )

    return render_template(

        "profile.html",

        customer=customer,

        history=history,

        total_amount=total_amount,

        print_mode=False,

        edit_mode=True,

        edit_batch=batch,

        edit_transactions=transactions
    )


# =========================================================
# UPDATE TRANSACTION BATCH
# =========================================================

@app.route(
    "/update-transaction/<int:batch_id>",
    methods=["POST"]
)
@login_required
def update_transaction(batch_id):

    conn = get_db()

    batch = conn.execute("""
        SELECT *
        FROM transaction_batches

        WHERE id = ?
    """, (batch_id,)).fetchone()

    if batch is None:

        conn.close()

        return (
            "Transaction history not found."
        )

    customer_id = batch["customer_id"]

    old_batch_total = float(
        batch["total_amount"] or 0
    )

    transaction_ids = request.form.getlist(
        "transaction_id[]"
    )

    amounts = request.form.getlist(
        "amount[]"
    )

    dates = request.form.getlist(
        "transaction_datetime[]"
    )

    utr_numbers = request.form.getlist(
        "utr_number[]"
    )

    sites = request.form.getlist(
        "site[]"
    )

    deleted_ids = request.form.getlist(
        "deleted_transaction_id[]"
    )

    # -----------------------------------------------------
    # DELETE SELECTED TRANSACTIONS
    # -----------------------------------------------------

    for deleted_id in deleted_ids:

        if not deleted_id:
            continue

        conn.execute("""
            DELETE FROM transactions

            WHERE id = ?

            AND batch_id = ?
        """, (
            deleted_id,
            batch_id
        ))

    # -----------------------------------------------------
    # UPDATE / INSERT
    # -----------------------------------------------------

    for index in range(len(amounts)):

        transaction_id = (
            transaction_ids[index]
            if index < len(transaction_ids)
            else ""
        ).strip()

        # Is transaction ko already delete kiya jaa chuka hai to skip karein
        if transaction_id and transaction_id in deleted_ids:
            continue

        amount_text = (
            amounts[index]
            if index < len(amounts)
            else "0"
        ).strip()

        date_value = (
            dates[index]
            if index < len(dates)
            else ""
        ).strip()

        utr_value = (
            utr_numbers[index]
            if index < len(utr_numbers)
            else ""
        ).strip()

        site_value = (
            sites[index]
            if index < len(sites)
            else ""
        ).strip()

        try:

            amount = float(
                amount_text or 0
            )

        except ValueError:

            amount = 0

        if amount < 0:
            amount = 0

        if transaction_id:

            conn.execute("""
                UPDATE transactions

                SET
                    amount = ?,
                    transaction_datetime = ?,
                    utr_number = ?,
                    site = ?

                WHERE id = ?

                AND batch_id = ?
            """, (
                amount,
                date_value,
                utr_value,
                site_value,
                transaction_id,
                batch_id
            ))

        else:

            if (
                not amount_text
                and not date_value
                and not utr_value
                and not site_value
            ):
                continue

            conn.execute("""
                INSERT INTO transactions
                (
                    batch_id,
                    customer_id,
                    amount,
                    transaction_datetime,
                    utr_number,
                    site
                )

                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                batch_id,
                customer_id,
                amount,
                date_value,
                utr_value,
                site_value
            ))

    # -----------------------------------------------------
    # NEW TOTAL
    # -----------------------------------------------------

    result = conn.execute("""
        SELECT
            COALESCE(SUM(amount), 0) AS total

        FROM transactions

        WHERE batch_id = ?
    """, (batch_id,)).fetchone()

    new_total = float(
        result["total"] or 0
    )

    # -----------------------------------------------------
    # CAPITAL DIFFERENCE
    # -----------------------------------------------------

    difference = (
        new_total - old_batch_total
    )

    current_capital_row = conn.execute("""
        SELECT amount

        FROM capital

        WHERE id = 1
    """).fetchone()

    current_capital = float(
        current_capital_row["amount"] or 0
    )

    if difference > 0:

        if difference > current_capital:

            conn.rollback()
            conn.close()

            return (
                f"Update nahi hua. "
                f"Capital ₹{current_capital:.2f} hai, "
                f"aur ₹{difference:.2f} extra chahiye."
            )

        new_capital = (
            current_capital - difference
        )

    elif difference < 0:

        new_capital = (
            current_capital + abs(difference)
        )

    else:

        new_capital = current_capital

    # -----------------------------------------------------
    # UPDATE BATCH TOTAL
    # -----------------------------------------------------

    conn.execute("""
        UPDATE transaction_batches

        SET total_amount = ?

        WHERE id = ?
    """, (
        new_total,
        batch_id
    ))

    # -----------------------------------------------------
    # UPDATE CAPITAL
    # -----------------------------------------------------

    conn.execute("""
        UPDATE capital

        SET amount = ?

        WHERE id = 1
    """, (
        new_capital,
    ))

    conn.commit()
    conn.close()

    return redirect(
        url_for(
            "profile",
            id=customer_id
        )
    )


# =========================================================
# PRINT TRANSACTION
# =========================================================

@app.route(
    "/print-transaction/<int:batch_id>"
)
@login_required
def print_transaction(batch_id):

    conn = get_db()

    batch = conn.execute("""
        SELECT *
        FROM transaction_batches

        WHERE id = ?
    """, (batch_id,)).fetchone()

    if batch is None:

        conn.close()

        return (
            "Transaction history not found."
        )

    customer = conn.execute("""
        SELECT *
        FROM customers

        WHERE id = ?
    """, (batch["customer_id"],)).fetchone()

    transactions = conn.execute("""
        SELECT *
        FROM transactions

        WHERE batch_id = ?

        ORDER BY id ASC
    """, (batch_id,)).fetchall()

    conn.close()

    if customer is None:

        return "Customer not found."

    return render_template(

        "profile.html",

        customer=customer,

        history=[],

        total_amount=batch["total_amount"],

        print_mode=True,

        edit_mode=False,

        print_batch=batch,

        print_transactions=transactions
    )


# =========================================================
# DELETE TRANSACTION BATCH
# =========================================================

@app.route(
    "/delete-transaction-batch/<int:batch_id>",
    methods=["POST"]
)
@login_required
def delete_transaction_batch(batch_id):

    conn = get_db()

    batch = conn.execute("""
        SELECT
            customer_id,
            total_amount

        FROM transaction_batches

        WHERE id = ?
    """, (batch_id,)).fetchone()

    if batch is None:

        conn.close()

        return redirect(
            url_for("home")
        )

    customer_id = batch["customer_id"]

    batch_total = float(
        batch["total_amount"] or 0
    )

    # Agar is batch pe pehle se refund ho chuka hai,
    # to wo amount capital mein already wapas aa chuka
    # hai — isliye sirf बाकी (total - refund) hi wapas
    # jodenge, taaki double-count na ho.

    total_refund = get_total_refund_for_batch(
        batch_id
    )

    refundable_amount = batch_total - total_refund

    if refundable_amount < 0:
        refundable_amount = 0

    # Refunds delete (batch khud hi delete ho raha hai)

    conn.execute("""
        DELETE FROM refunds

        WHERE batch_id = ?
    """, (batch_id,))

    # Transactions delete

    conn.execute("""
        DELETE FROM transactions

        WHERE batch_id = ?
    """, (batch_id,))

    # Batch delete

    conn.execute("""
        DELETE FROM transaction_batches

        WHERE id = ?
    """, (batch_id,))

    # Capital वापस जोड़ना

    conn.execute("""
        UPDATE capital

        SET amount = amount + ?

        WHERE id = 1
    """, (refundable_amount,))

    conn.commit()
    conn.close()

    return redirect(
        url_for(
            "profile",
            id=customer_id
        )
    )


# =========================================================
# ADD REFUND
# =========================================================

@app.route(
    "/add-refund/<int:batch_id>",
    methods=["POST"]
)
@login_required
def add_refund(batch_id):

    conn = get_db()

    batch = conn.execute("""
        SELECT
            id,
            customer_id,
            total_amount

        FROM transaction_batches

        WHERE id = ?
    """, (batch_id,)).fetchone()

    if batch is None:

        conn.close()

        return "Transaction history not found."

    customer_id = batch["customer_id"]

    amount_text = request.form.get(
        "refund_amount",
        ""
    ).strip()

    transaction_datetime = request.form.get(
        "refund_datetime",
        ""
    ).strip()

    remarks = request.form.get(
        "refund_remarks",
        ""
    ).strip()

    try:

        amount = float(
            amount_text or 0
        )

    except ValueError:

        conn.close()

        return (
            "Invalid Refund Amount. "
            "<a href='/profile/"
            + str(customer_id)
            + "'>Back</a>"
        )

    if amount <= 0:

        conn.close()

        return (
            "Refund Amount must be greater than 0. "
            "<a href='/profile/"
            + str(customer_id)
            + "'>Back</a>"
        )

    # -----------------------------------------------------
    # REFUND, BATCH KE TOTAL SE ZYADA NAHI HO SAKTA
    # -----------------------------------------------------

    batch_total = float(
        batch["total_amount"] or 0
    )

    already_refunded = get_total_refund_for_batch(
        batch_id
    )

    remaining_refundable = (
        batch_total - already_refunded
    )

    if amount > remaining_refundable:

        conn.close()

        return (
            f"Refund nahi hua. Is transaction ka "
            f"total ₹{batch_total:.2f} hai, "
            f"pehle se ₹{already_refunded:.2f} refund "
            f"ho chuka hai. Sirf ₹{remaining_refundable:.2f} "
            f"tak hi refund kiya ja sakta hai. "
            f"<br><a href='/profile/{customer_id}'>Back</a>"
        )

    # -----------------------------------------------------
    # DATE & TIME
    # -----------------------------------------------------

    if not transaction_datetime:

        transaction_datetime = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    else:

        transaction_datetime = transaction_datetime.replace(
            "T",
            " "
        )

        parsed = None

        formats = [

            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",

            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y %H:%M",

            "%d-%m-%Y %H:%M:%S",
            "%d-%m-%Y %H:%M",

            "%d.%m.%Y %H:%M:%S",
            "%d.%m.%Y %H:%M"
        ]

        for fmt in formats:

            try:

                parsed = datetime.strptime(
                    transaction_datetime,
                    fmt
                )

                break

            except ValueError:
                pass

        if parsed is None:

            conn.close()

            return (
                "Invalid Date & Time. "
                "Example: 28/08/2026 07:30 PM "
                f"<br><a href='/profile/{customer_id}'>Back</a>"
            )

        transaction_datetime = parsed.strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    # -----------------------------------------------------
    # SAVE REFUND
    # -----------------------------------------------------

    try:

        conn.execute("""
            INSERT INTO refunds
            (
                batch_id,
                customer_id,
                amount,
                transaction_datetime,
                remarks
            )

            VALUES (?, ?, ?, ?, ?)
        """, (
            batch_id,
            customer_id,
            amount,
            transaction_datetime,
            remarks
        ))

        conn.execute("""
            UPDATE capital

            SET amount = amount + ?

            WHERE id = 1
        """, (amount,))

        conn.commit()

    except Exception as e:

        conn.rollback()
        conn.close()

        return (
            "Refund save karte time error aa gaya. "
            f"<br>{e}"
            f"<br><a href='/profile/{customer_id}'>Back</a>"
        )

    conn.close()

    return redirect(
        url_for(
            "profile",
            id=customer_id
        )
    )


# =========================================================
# DELETE REFUND
# =========================================================

@app.route(
    "/delete-refund/<int:refund_id>",
    methods=["POST"]
)
@login_required
def delete_refund(refund_id):

    conn = get_db()

    refund = conn.execute("""
        SELECT
            id,
            customer_id,
            amount

        FROM refunds

        WHERE id = ?
    """, (refund_id,)).fetchone()

    if refund is None:

        conn.close()

        return redirect(
            url_for("home")
        )

    customer_id = refund["customer_id"]

    amount = float(
        refund["amount"] or 0
    )

    capital_row = conn.execute("""
        SELECT amount

        FROM capital

        WHERE id = 1
    """).fetchone()

    current_capital = float(
        capital_row["amount"] or 0
    )

    if amount > current_capital:

        conn.close()

        return (
            f"Refund delete nahi hua. "
            f"Current Capital ₹{current_capital:.2f} hai, "
            f"lekin refund amount ₹{amount:.2f} hai. "
            f"<br><a href='/profile/{customer_id}'>Back</a>"
        )

    try:

        conn.execute("""
            DELETE FROM refunds

            WHERE id = ?
        """, (refund_id,))

        conn.execute("""
            UPDATE capital

            SET amount = amount - ?

            WHERE id = 1
        """, (amount,))

        conn.commit()

    except Exception:

        conn.rollback()
        conn.close()

        return (
            "Refund delete karte time error aa gaya. "
            f"<a href='/profile/{customer_id}'>Back</a>"
        )

    conn.close()

    return redirect(
        url_for(
            "profile",
            id=customer_id
        )
    )

@app.route(
    "/edit-account/<int:id>"
)
@login_required
def edit_account(id):

    customer = get_customer(id)

    if customer is None:

        return "Account not found."

    return render_template(
        "edit.html",
        customer=customer
    )


# =========================================================
# UPDATE ACCOUNT
# =========================================================

@app.route(
    "/update-account/<int:id>",
    methods=["POST"]
)
@login_required
def update_account(id):

    account_number = request.form.get(
        "account_number",
        ""
    ).strip()

    name = request.form.get(
        "name",
        ""
    ).strip()

    mobile = request.form.get(
        "mobile",
        ""
    ).strip()

    bank_name = request.form.get(
        "bank_name",
        ""
    ).strip()

    ifsc_code = request.form.get(
        "ifsc_code",
        ""
    ).strip().upper()

    if not account_number or not name:

        flash(
            "Account Number and Name are required.",
            "error"
        )

        return redirect(
            url_for(
                "edit_account",
                id=id
            )
        )

    if not is_valid_mobile_number(mobile):

        flash(
            "Mobile Number invalid hai. "
            "10 digit ka valid Indian number "
            "(6-9 se start) dalein, ya khaali chodein.",
            "error"
        )

        return redirect(
            url_for(
                "edit_account",
                id=id
            )
        )

    if not is_valid_ifsc_code(ifsc_code):

        flash(
            "IFSC Code invalid hai. Format: "
            "4 letters + 0 + 6 alphanumeric "
            "(jaise SBIN0001234), ya khaali chodein.",
            "error"
        )

        return redirect(
            url_for(
                "edit_account",
                id=id
            )
        )

    conn = get_db()

    try:

        conn.execute("""
            UPDATE customers

            SET
                account_number = ?,
                name = ?,
                mobile = ?,
                bank_name = ?,
                ifsc_code = ?

            WHERE id = ?
        """, (
            account_number,
            name,
            mobile,
            bank_name,
            ifsc_code,
            id
        ))

        conn.commit()

    except sqlite3.IntegrityError:

        conn.close()

        flash(
            "This Account Number already exists.",
            "error"
        )

        return redirect(
            url_for(
                "edit_account",
                id=id
            )
        )

    conn.close()

    flash(
        "Account updated successfully.",
        "success"
    )

    return redirect(
        url_for(
            "profile",
            id=id
        )
    )


# =========================================================
# DELETE ACCOUNT
# =========================================================

@app.route(
    "/delete-account/<int:id>",
    methods=["POST"]
)
@login_required
def delete_account(id):

    conn = get_db()

    # पहले इस customer के transactions का total

    result = conn.execute("""
        SELECT
            COALESCE(SUM(amount), 0) AS total

        FROM transactions

        WHERE customer_id = ?
    """, (id,)).fetchone()

    refund_amount = float(
        result["total"] or 0
    )

    # Transactions delete

    conn.execute("""
        DELETE FROM transactions

        WHERE customer_id = ?
    """, (id,))

    # Batches delete

    conn.execute("""
        DELETE FROM transaction_batches

        WHERE customer_id = ?
    """, (id,))

    # Customer delete

    conn.execute("""
        DELETE FROM customers

        WHERE id = ?
    """, (id,))

    # Capital वापस

    conn.execute("""
        UPDATE capital

        SET amount = amount + ?

        WHERE id = 1
    """, (refund_amount,))

    conn.commit()
    conn.close()

    return redirect(
        url_for("home")
    )


# =========================================================
# TODAY TRANSACTIONS
# =========================================================

@app.route("/today-transactions")
@login_required
def today_transactions():

    today = datetime.now().strftime(
        "%Y-%m-%d"
    )

    rows = get_transactions_by_date(
        today
    )

    total_transactions = len(rows)

    total_amount = sum(
        float(row["amount"] or 0)
        for row in rows
    )

    return render_template(

        "transactions.html",

        transactions=rows,

        total_transactions=total_transactions,

        transaction_count=total_transactions,

        total_amount=total_amount,

        selected_date=today,

        report_title="Today's Transactions"
    )


# =========================================================
# TRANSACTION REPORT BY DATE
# =========================================================

@app.route("/transaction-report")
@login_required
def transaction_report():

    selected_date = request.args.get(
        "date",
        ""
    ).strip()

    if not selected_date:

        selected_date = datetime.now().strftime(
            "%Y-%m-%d"
        )

    rows = get_transactions_by_date(
        selected_date
    )

    total_transactions = len(rows)

    total_amount = sum(
        float(row["amount"] or 0)
        for row in rows
    )

    return render_template(

        "transactions.html",

        transactions=rows,

        total_transactions=total_transactions,

        transaction_count=total_transactions,

        total_amount=total_amount,

        selected_date=selected_date,

        report_title="Transaction Report"
    )

# =========================================================
# TRANSACTION REPORT - DATE RANGE
# =========================================================

@app.route("/transaction-report-range")
@login_required
def transaction_report_range():

    start_date = request.args.get(
        "start_date",
        ""
    ).strip()

    end_date = request.args.get(
        "end_date",
        ""
    ).strip()

    if not start_date:
        start_date = datetime.now().strftime(
            "%Y-%m-%d"
        )

    if not end_date:
        end_date = start_date

    rows = get_transactions_by_date_range(
        start_date,
        end_date
    )

    total_transactions = len(rows)

    total_amount = 0.0

    for row in rows:

        try:
            amount = float(
                row["amount"] or 0
            )
        except (ValueError, TypeError):
            amount = 0.0

        total_amount += amount

    return render_template(
        "transactions.html",

        transactions=rows,

        total_transactions=total_transactions,

        transaction_count=total_transactions,

        total_amount=total_amount,

        selected_date=start_date,

        start_date=start_date,

        end_date=end_date,

        report_title="Transaction Report"
    )


# =========================================================
# TODAY TRANSACTIONS REPORT
# =========================================================

@app.route("/transaction-report-today")
@login_required
def transaction_report_today():

    today = datetime.now().strftime(
        "%Y-%m-%d"
    )

    rows = get_transactions_by_date_range(
        today,
        today
    )

    total_transactions = len(rows)

    total_amount = 0.0

    for row in rows:

        try:
            amount = float(
                row["amount"] or 0
            )
        except (ValueError, TypeError):
            amount = 0.0

        total_amount += amount

    return render_template(
        "transactions.html",

        transactions=rows,

        total_transactions=total_transactions,

        transaction_count=total_transactions,

        total_amount=total_amount,

        selected_date=today,

        start_date=today,

        end_date=today,

        report_title="Today's Transactions"
    )


# =========================================================
# 1 MONTH TRANSACTIONS
# =========================================================

@app.route("/transaction-report-1-month")
@login_required
def transaction_report_1_month():

    end_date = datetime.now().date()

    start_date = end_date.replace(
        month=end_date.month,
        day=1
    )

    start_date = start_date.strftime(
        "%Y-%m-%d"
    )

    end_date = end_date.strftime(
        "%Y-%m-%d"
    )

    rows = get_transactions_by_date_range(
        start_date,
        end_date
    )

    total_transactions = len(rows)

    total_amount = sum(
        float(row["amount"] or 0)
        for row in rows
    )

    return render_template(
        "transactions.html",

        transactions=rows,

        total_transactions=total_transactions,

        transaction_count=total_transactions,

        total_amount=total_amount,

        selected_date=start_date,

        start_date=start_date,

        end_date=end_date,

        report_title="1 Month Transactions"
    )


# =========================================================
# 2 MONTHS TRANSACTIONS
# =========================================================

# =========================================================
# 3 MONTHS TRANSACTIONS
# =========================================================

@app.route("/transaction-report-3-months")
@login_required
def transaction_report_3_months():

    end_date = datetime.now().date()

    total_months = (
        end_date.year * 12
        + end_date.month
        - 1
    )

    total_months -= 3

    year = total_months // 12

    month = total_months % 12 + 1

    start_date = date(
        year,
        month,
        1
    )

    start_date = start_date.strftime(
        "%Y-%m-%d"
    )

    end_date = end_date.strftime(
        "%Y-%m-%d"
    )

    rows = get_transactions_by_date_range(
        start_date,
        end_date
    )

    total_transactions = len(rows)

    total_amount = sum(
        float(row["amount"] or 0)
        for row in rows
    )

    return render_template(
        "transactions.html",

        transactions=rows,

        total_transactions=total_transactions,

        transaction_count=total_transactions,

        total_amount=total_amount,

        selected_date=start_date,

        start_date=start_date,

        end_date=end_date,

        report_title="3 Months Transactions"
    )


# =========================================================
# 6 MONTHS TRANSACTIONS
# =========================================================

@app.route("/transaction-report-6-months")
@login_required
def transaction_report_6_months():

    end_date = datetime.now().date()

    total_months = (
        end_date.year * 12
        + end_date.month
        - 1
    )

    total_months -= 6

    year = total_months // 12

    month = total_months % 12 + 1

    start_date = date(
        year,
        month,
        1
    )

    start_date = start_date.strftime(
        "%Y-%m-%d"
    )

    end_date = end_date.strftime(
        "%Y-%m-%d"
    )

    rows = get_transactions_by_date_range(
        start_date,
        end_date
    )

    total_transactions = len(rows)

    total_amount = sum(
        float(row["amount"] or 0)
        for row in rows
    )

    return render_template(
        "transactions.html",

        transactions=rows,

        total_transactions=total_transactions,

        transaction_count=total_transactions,

        total_amount=total_amount,

        selected_date=start_date,

        start_date=start_date,

        end_date=end_date,

        report_title="6 Months Transactions"
    )


# =========================================================
# LEDGER STATEMENT
# =========================================================

# =========================================================
# BUILD LEDGER DATA (shared by page view + Excel/PDF export)
# =========================================================

def build_ledger_data(from_date, to_date):

    conn = get_db()

    # -----------------------------------------------------
    # CAPITAL ADDITIONS (CREDIT ENTRIES)
    # -----------------------------------------------------

    capital_rows = conn.execute("""
        SELECT
            amount,
            transaction_datetime,
            site,
            payment_type,
            remarks

        FROM capital_history
    """).fetchall()

    # -----------------------------------------------------
    # TRANSACTIONS / PAYOUTS (DEBIT ENTRIES)
    # -----------------------------------------------------

    transaction_rows = conn.execute("""
        SELECT
            transactions.amount,
            transactions.transaction_datetime,
            transactions.utr_number,
            transactions.site,

            customers.name,
            customers.account_number

        FROM transactions

        LEFT JOIN customers
            ON customers.id = transactions.customer_id
    """).fetchall()

    # -----------------------------------------------------
    # RECHARGES (DEBIT ENTRIES)
    # -----------------------------------------------------

    recharge_rows = conn.execute("""
        SELECT
            amount,
            transaction_datetime,
            mobile_number,
            operator,
            circle,
            remarks

        FROM recharges
    """).fetchall()

    # -----------------------------------------------------
    # REFUNDS (CREDIT ENTRIES)
    # -----------------------------------------------------

    refund_rows = conn.execute("""
        SELECT
            refunds.amount,
            refunds.transaction_datetime,
            refunds.batch_id,
            refunds.remarks,

            customers.name,
            customers.account_number

        FROM refunds

        LEFT JOIN customers
            ON customers.id = refunds.customer_id
    """).fetchall()

    # -----------------------------------------------------
    # RECHARGE REFUNDS (CREDIT ENTRIES)
    # -----------------------------------------------------

    recharge_refund_rows = conn.execute("""
        SELECT
            recharge_refunds.amount,
            recharge_refunds.transaction_datetime,
            recharge_refunds.remarks,

            recharges.mobile_number,
            recharges.operator

        FROM recharge_refunds

        LEFT JOIN recharges
            ON recharges.id = recharge_refunds.recharge_id
    """).fetchall()

    conn.close()

    # -----------------------------------------------------
    # BUILD COMBINED ENTRY LIST
    # -----------------------------------------------------

    entries = []

    for row in capital_rows:

        particular = "Capital Received"

        if row["payment_type"]:

            particular += (
                " - " + row["payment_type"]
            )

        if row["remarks"]:

            particular += (
                " (" + row["remarks"] + ")"
            )

        entries.append({

            "datetime_obj": parse_datetime_for_sort(
                row["transaction_datetime"]
            ),

            "date_display": row["transaction_datetime"],

            "particular": particular,

            "site": row["site"] or "-",

            "debit": 0.0,

            "credit": float(row["amount"] or 0)
        })

    for row in transaction_rows:

        if row["name"]:

            customer_label = (
                row["name"]
                + " ("
                + (row["account_number"] or "")
                + ")"
            )

        else:

            customer_label = "Unknown Customer"

        particular = "Payment to " + customer_label

        if row["utr_number"]:

            particular += (
                " - UTR " + row["utr_number"]
            )

        entries.append({

            "datetime_obj": parse_datetime_for_sort(
                row["transaction_datetime"]
            ),

            "date_display": row["transaction_datetime"],

            "particular": particular,

            "site": row["site"] or "-",

            "debit": float(row["amount"] or 0),

            "credit": 0.0
        })

    for row in recharge_rows:

        particular = (
            "Mobile Recharge - "
            + row["mobile_number"]
        )

        if row["operator"]:

            particular += (
                " (" + row["operator"] + ")"
            )

        if row["circle"]:

            particular += (
                " - " + row["circle"]
            )

        if row["remarks"]:

            particular += (
                " - " + row["remarks"]
            )

        entries.append({

            "datetime_obj": parse_datetime_for_sort(
                row["transaction_datetime"]
            ),

            "date_display": row["transaction_datetime"],

            "particular": particular,

            "site": "-",

            "debit": float(row["amount"] or 0),

            "credit": 0.0
        })

    for row in refund_rows:

        if row["name"]:

            customer_label = (
                row["name"]
                + " ("
                + (row["account_number"] or "")
                + ")"
            )

        else:

            customer_label = "Unknown Customer"

        particular = (
            "Refund from "
            + customer_label
            + " - Transaction #"
            + str(row["batch_id"])
        )

        if row["remarks"]:

            particular += (
                " - " + row["remarks"]
            )

        entries.append({

            "datetime_obj": parse_datetime_for_sort(
                row["transaction_datetime"]
            ),

            "date_display": row["transaction_datetime"],

            "particular": particular,

            "site": "-",

            "debit": 0.0,

            "credit": float(row["amount"] or 0)
        })

    for row in recharge_refund_rows:

        particular = (
            "Refund - Mobile Recharge "
            + (row["mobile_number"] or "")
        )

        if row["operator"]:

            particular += (
                " (" + row["operator"] + ")"
            )

        if row["remarks"]:

            particular += (
                " - " + row["remarks"]
            )

        entries.append({

            "datetime_obj": parse_datetime_for_sort(
                row["transaction_datetime"]
            ),

            "date_display": row["transaction_datetime"],

            "particular": particular,

            "site": "-",

            "debit": 0.0,

            "credit": float(row["amount"] or 0)
        })

    # -----------------------------------------------------
    # SORT CHRONOLOGICALLY (OLDEST FIRST)
    # -----------------------------------------------------

    entries.sort(
        key=lambda e: (
            e["datetime_obj"] or datetime.min
        )
    )

    # -----------------------------------------------------
    # RUNNING BALANCE
    # (Anchored to the ACTUAL current capital, so the
    # ledger's closing balance always matches the Capital
    # page — even if some old adjustment exists that isn't
    # tracked in capital_history/transactions.)
    # -----------------------------------------------------

    total_credit_all = sum(
        e["credit"] for e in entries
    )

    total_debit_all = sum(
        e["debit"] for e in entries
    )

    current_capital = get_capital()

    starting_balance = (
        current_capital
        - total_credit_all
        + total_debit_all
    )

    running_balance = starting_balance

    for entry in entries:

        running_balance += entry["credit"]
        running_balance -= entry["debit"]

        entry["balance"] = running_balance

    # -----------------------------------------------------
    # DATE RANGE FILTER (balance already computed above,
    # so filtering the view doesn't break the running total)
    # -----------------------------------------------------

    normalized_from = (
        normalize_transaction_date(from_date)
        if from_date else None
    )

    normalized_to = (
        normalize_transaction_date(to_date)
        if to_date else None
    )

    if normalized_from or normalized_to:

        filtered_entries = []

        for entry in entries:

            entry_date = (
                entry["datetime_obj"].strftime("%Y-%m-%d")
                if entry["datetime_obj"] else None
            )

            if entry_date is None:
                continue

            if normalized_from and entry_date < normalized_from:
                continue

            if normalized_to and entry_date > normalized_to:
                continue

            filtered_entries.append(entry)

    else:

        filtered_entries = entries

    # -----------------------------------------------------
    # OPENING BALANCE (balance just before the filtered view)
    # -----------------------------------------------------

    if filtered_entries and normalized_from:

        opening_balance = (
            filtered_entries[0]["balance"]
            - filtered_entries[0]["credit"]
            + filtered_entries[0]["debit"]
        )

    else:

        opening_balance = starting_balance

    total_debit = sum(
        e["debit"] for e in filtered_entries
    )

    total_credit = sum(
        e["credit"] for e in filtered_entries
    )

    closing_balance = (
        filtered_entries[-1]["balance"]
        if filtered_entries else opening_balance
    )

    # -----------------------------------------------------
    # DISPLAY ORDER: newest entry pehle (balance values
    # already calculated chronologically upar, isliye
    # sirf display ke liye list reverse kar rahe hain)
    # -----------------------------------------------------

    display_entries = list(
        reversed(filtered_entries)
    )

    return {

        "entries": display_entries,

        "opening_balance": opening_balance,

        "closing_balance": closing_balance,

        "total_debit": total_debit,

        "total_credit": total_credit
    }


# =========================================================
# LEDGER STATEMENT (Overall Capital Ledger)
# =========================================================

@app.route("/ledger")
@login_required
def ledger():

    from_date = request.args.get(
        "from_date",
        ""
    ).strip()

    to_date = request.args.get(
        "to_date",
        ""
    ).strip()

    ledger_data = build_ledger_data(
        from_date,
        to_date
    )

    return render_template(

        "ledger.html",

        entries=ledger_data["entries"],

        from_date=from_date,

        to_date=to_date,

        opening_balance=ledger_data["opening_balance"],

        closing_balance=ledger_data["closing_balance"],

        total_debit=ledger_data["total_debit"],

        total_credit=ledger_data["total_credit"]
    )


# =========================================================
# DASHBOARD (Charts / Analytics)
# =========================================================

@app.route("/dashboard")
@login_required
def dashboard():

    ledger_data = build_ledger_data("", "")

    # Chronological order chahiye (oldest first) grouping
    # ke liye — ledger_data mein entries newest-first hain

    chronological_entries = list(
        reversed(ledger_data["entries"])
    )

    # -----------------------------------------------------
    # MONTH-WISE AGGREGATION
    # -----------------------------------------------------

    monthly = {}

    month_order = []

    for entry in chronological_entries:

        if entry["datetime_obj"]:

            month_key = entry["datetime_obj"].strftime(
                "%Y-%m"
            )

        else:

            month_key = "Unknown"

        if month_key not in monthly:

            monthly[month_key] = {
                "debit": 0.0,
                "credit": 0.0,
                "balance": 0.0
            }

            month_order.append(month_key)

        monthly[month_key]["debit"] += entry["debit"]
        monthly[month_key]["credit"] += entry["credit"]
        monthly[month_key]["balance"] = entry["balance"]

    # Last 6 months (agar itna data ho)

    last_months = [
        m for m in month_order if m != "Unknown"
    ][-6:]

    month_labels = []

    for month_key in last_months:

        try:

            parsed_month = datetime.strptime(
                month_key,
                "%Y-%m"
            )

            month_labels.append(
                parsed_month.strftime("%b %Y")
            )

        except ValueError:

            month_labels.append(month_key)

    monthly_debit = [
        round(monthly[m]["debit"], 2)
        for m in last_months
    ]

    monthly_credit = [
        round(monthly[m]["credit"], 2)
        for m in last_months
    ]

    monthly_balance = [
        round(monthly[m]["balance"], 2)
        for m in last_months
    ]

    # -----------------------------------------------------
    # CURRENT MONTH BREAKDOWN (Debit type-wise)
    # -----------------------------------------------------

    current_month_key = datetime.now().strftime("%Y-%m")

    payment_total = 0.0
    recharge_total = 0.0
    other_debit_total = 0.0

    for entry in chronological_entries:

        if not entry["datetime_obj"]:
            continue

        if entry["datetime_obj"].strftime("%Y-%m") != current_month_key:
            continue

        if entry["debit"] <= 0:
            continue

        if entry["particular"].startswith("Payment to"):

            payment_total += entry["debit"]

        elif entry["particular"].startswith("Mobile Recharge"):

            recharge_total += entry["debit"]

        else:

            other_debit_total += entry["debit"]

    return render_template(

        "dashboard.html",

        month_labels=month_labels,

        monthly_debit=monthly_debit,

        monthly_credit=monthly_credit,

        monthly_balance=monthly_balance,

        payment_total=round(payment_total, 2),

        recharge_total=round(recharge_total, 2),

        other_debit_total=round(other_debit_total, 2),

        current_capital=get_capital()
    )


# =========================================================
# LEDGER EXPORT - EXCEL
# =========================================================

@app.route("/ledger/export/excel")
@login_required
def ledger_export_excel():

    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment

    from_date = request.args.get(
        "from_date",
        ""
    ).strip()

    to_date = request.args.get(
        "to_date",
        ""
    ).strip()

    ledger_data = build_ledger_data(
        from_date,
        to_date
    )

    workbook = openpyxl.Workbook()

    sheet = workbook.active

    sheet.title = "Capital Ledger"

    # -----------------------------------------------------
    # HEADER
    # -----------------------------------------------------

    sheet.merge_cells("A1:F1")

    sheet["A1"] = "Capital Ledger Statement"

    sheet["A1"].font = Font(
        size=16,
        bold=True
    )

    sheet["A1"].alignment = Alignment(
        horizontal="center"
    )

    period_text = "All Time"

    if from_date or to_date:

        period_text = (
            (from_date or "Start")
            + " to "
            + (to_date or "Today")
        )

    sheet.merge_cells("A2:F2")

    sheet["A2"] = "Period: " + period_text

    sheet["A2"].alignment = Alignment(
        horizontal="center"
    )

    # -----------------------------------------------------
    # SUMMARY ROW
    # -----------------------------------------------------

    sheet["A4"] = "Opening Balance"
    sheet["B4"] = ledger_data["opening_balance"]

    sheet["C4"] = "Total Debit"
    sheet["D4"] = ledger_data["total_debit"]

    sheet["E4"] = "Total Credit"
    sheet["F4"] = ledger_data["total_credit"]

    sheet["A5"] = "Closing Balance"
    sheet["B5"] = ledger_data["closing_balance"]

    for cell in ["A4", "C4", "E4", "A5"]:
        sheet[cell].font = Font(bold=True)

    # -----------------------------------------------------
    # TABLE HEADER
    # -----------------------------------------------------

    header_row = 7

    headers = [
        "Date & Time", "Particular", "Site",
        "Debit (₹)", "Credit (₹)", "Balance (₹)"
    ]

    header_fill = PatternFill(
        start_color="111827",
        end_color="111827",
        fill_type="solid"
    )

    for col_index, header_text in enumerate(headers, start=1):

        cell = sheet.cell(
            row=header_row,
            column=col_index,
            value=header_text
        )

        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")

    # -----------------------------------------------------
    # TABLE ROWS
    # -----------------------------------------------------

    current_row = header_row + 1

    for entry in ledger_data["entries"]:

        sheet.cell(
            row=current_row, column=1,
            value=entry["date_display"]
        )

        sheet.cell(
            row=current_row, column=2,
            value=entry["particular"]
        )

        sheet.cell(
            row=current_row, column=3,
            value=entry["site"]
        )

        sheet.cell(
            row=current_row, column=4,
            value=(
                entry["debit"]
                if entry["debit"] > 0 else None
            )
        )

        sheet.cell(
            row=current_row, column=5,
            value=(
                entry["credit"]
                if entry["credit"] > 0 else None
            )
        )

        sheet.cell(
            row=current_row, column=6,
            value=entry["balance"]
        )

        current_row += 1

    # Opening balance row at bottom (matches on-screen order)

    sheet.cell(
        row=current_row, column=2,
        value="Opening Balance"
    )

    sheet.cell(
        row=current_row, column=6,
        value=ledger_data["opening_balance"]
    )

    for col in range(1, 7):
        sheet.cell(row=current_row, column=col).font = Font(bold=True)

    # -----------------------------------------------------
    # COLUMN WIDTHS
    # -----------------------------------------------------

    column_widths = [20, 45, 15, 14, 14, 16]

    for col_index, width in enumerate(column_widths, start=1):

        sheet.column_dimensions[
            openpyxl.utils.get_column_letter(col_index)
        ].width = width

    # -----------------------------------------------------
    # SAVE TO MEMORY AND SEND
    # -----------------------------------------------------

    output = BytesIO()

    workbook.save(output)

    output.seek(0)

    filename = (
        "Ledger_Statement_"
        + datetime.now().strftime("%Y-%m-%d")
        + ".xlsx"
    )

    return send_file(
        output,
        mimetype=(
            "application/vnd.openxmlformats-officedocument"
            ".spreadsheetml.sheet"
        ),
        as_attachment=True,
        download_name=filename
    )


# =========================================================
# LEDGER EXPORT - PDF
# =========================================================

@app.route("/ledger/export/pdf")
@login_required
def ledger_export_pdf():

    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate,
        Table,
        TableStyle,
        Paragraph,
        Spacer
    )
    from reportlab.lib.styles import getSampleStyleSheet

    from_date = request.args.get(
        "from_date",
        ""
    ).strip()

    to_date = request.args.get(
        "to_date",
        ""
    ).strip()

    ledger_data = build_ledger_data(
        from_date,
        to_date
    )

    output = BytesIO()

    doc = SimpleDocTemplate(
        output,
        pagesize=landscape(A4),
        topMargin=15 * mm,
        bottomMargin=15 * mm
    )

    styles = getSampleStyleSheet()

    elements = []

    elements.append(
        Paragraph(
            "Capital Ledger Statement",
            styles["Title"]
        )
    )

    period_text = "All Time"

    if from_date or to_date:

        period_text = (
            (from_date or "Start")
            + " to "
            + (to_date or "Today")
        )

    elements.append(
        Paragraph(
            "Period: " + period_text,
            styles["Normal"]
        )
    )

    elements.append(Spacer(1, 10))

    summary_text = (
        f"Opening Balance: ₹{ledger_data['opening_balance']:.2f}"
        f" &nbsp;|&nbsp; Total Debit: ₹{ledger_data['total_debit']:.2f}"
        f" &nbsp;|&nbsp; Total Credit: ₹{ledger_data['total_credit']:.2f}"
        f" &nbsp;|&nbsp; Closing Balance: ₹{ledger_data['closing_balance']:.2f}"
    )

    elements.append(
        Paragraph(summary_text, styles["Normal"])
    )

    elements.append(Spacer(1, 15))

    # -----------------------------------------------------
    # TABLE DATA
    # -----------------------------------------------------

    table_data = [
        ["Date & Time", "Particular", "Site",
         "Debit (₹)", "Credit (₹)", "Balance (₹)"]
    ]

    for entry in ledger_data["entries"]:

        table_data.append([
            entry["date_display"],
            entry["particular"],
            entry["site"],
            (
                f"{entry['debit']:.2f}"
                if entry["debit"] > 0 else "-"
            ),
            (
                f"{entry['credit']:.2f}"
                if entry["credit"] > 0 else "-"
            ),
            f"{entry['balance']:.2f}"
        ])

    table_data.append([
        "", "Opening Balance", "", "", "",
        f"{ledger_data['opening_balance']:.2f}"
    ])

    table = Table(
        table_data,
        colWidths=[
            30 * mm, 90 * mm, 25 * mm,
            25 * mm, 25 * mm, 28 * mm
        ],
        repeatRows=1
    )

    table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#111827")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d1d5db")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#f9fafb")]),
            ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#eef2ff")),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ("ALIGN", (3, 0), (5, -1), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ])
    )

    elements.append(table)

    doc.build(elements)

    output.seek(0)

    filename = (
        "Ledger_Statement_"
        + datetime.now().strftime("%Y-%m-%d")
        + ".pdf"
    )

    return send_file(
        output,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename
    )


# =========================================================
# START APP
# =========================================================

create_database()
add_missing_columns()
ensure_default_admin()


if __name__ == "__main__":

    app.run(
        debug=False
    )
