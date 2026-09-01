from flask import Flask, render_template, request, redirect, url_for
import sqlite3
from datetime import datetime, date
import re
import io

app = Flask(__name__)

DATABASE = "database.db"


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

    conn.commit()
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


def change_capital(amount):

    """
    amount positive = capital बढ़ेगा
    amount negative = capital घटेगा
    """

    conn = get_db()

    row = conn.execute("""
        SELECT amount
        FROM capital
        WHERE id = 1
    """).fetchone()

    current = float(row["amount"] or 0)

    new_amount = current + float(amount)

    if new_amount < 0:
        conn.close()
        return False

    conn.execute("""
        UPDATE capital
        SET amount = ?
        WHERE id = 1
    """, (new_amount,))

    conn.commit()
    conn.close()

    return True


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

    start_normalized = normalize_transaction_date(start_date)
    end_normalized = normalize_transaction_date(end_date)

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

        transaction_date = normalize_transaction_date(
            row["transaction_datetime"]
        )

        if not transaction_date:
            continue

        if start_normalized <= transaction_date <= end_normalized:
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

        history.append({

            "id": batch["id"],

            "customer_id": batch["customer_id"],

            "total_amount": batch["total_amount"],

            "created_at": batch["created_at"],

            "transactions": transactions
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
# HOME
# =========================================================

@app.route("/")
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

    return render_template(

        "index.html",

        customers=customers,

        customer=None,

        search_account="",

        search_name="",

        search_mobile="",

        total_accounts=total_accounts,

        capital=capital,

        today_transaction_amount=today_transaction_amount
    )


# =========================================================
# CAPITAL PAGE
# =========================================================

@app.route(
    "/capital",
    methods=["GET", "POST"]
)
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
        "PAID"
    ).strip().upper()

    allowed_payment_types = {
        "PAID BALANCE",
        "CREDIT BALANCE",
        "ADVANCE BALANCE"
    }

    if payment_type not in allowed_payment_types:
        payment_type = "PAID"

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
        "PAID"
    ).strip().upper()

    allowed_payment_types = {
        "PAID BALANCE",
        "CREDIT BALANCE",
        "ADVANCE BALANCE"
    }

    if payment_type not in allowed_payment_types:
        payment_type = "PAID"

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
# CAPITAL ALIAS
# =========================================================

@app.route(
    "/set-capital",
    methods=["GET", "POST"]
)
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

        return (
            "Account Number and Name are required."
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

        return (
            "This Account Number already exists."
        )

    conn.close()

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

    return render_template(

        "index.html",

        customers=customers,

        customer=customer,

        search_account=account_number,

        search_name=name,

        search_mobile=mobile,

        total_accounts=total_accounts,

        capital=capital,

        today_transaction_amount=today_transaction_amount
    )


# =========================================================
# TODAY / DATE-WISE TRANSACTIONS
# =========================================================

@app.route("/transactions")
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
@app.route("/ledger-statement")
def ledger_statement():
    return render_template("ledger_statement.html")
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
# CUSTOM DATE RANGE TRANSACTION REPORT
# =========================================================

@app.route("/transaction-range-report")
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
    """, (batch_total,))

    conn.commit()
    conn.close()

    return redirect(
        url_for(
            "profile",
            id=customer_id
        )
    )


# =========================================================
# EDIT ACCOUNT
# =========================================================

@app.route(
    "/edit-account/<int:id>"
)
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

        return (
            "Account Number and Name are required."
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

        return (
            "This Account Number already exists."
        )

    conn.close()

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

@app.route("/transaction-report-2-months")
def transaction_report_2_months():

    end_date = datetime.now().date()

    if end_date.month <= 2:

        year = end_date.year - 1

        month = 12 + end_date.month - 2

    else:

        year = end_date.year

        month = end_date.month - 2

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

        report_title="2 Months Transactions"
    )


# =========================================================
# 5 MONTHS TRANSACTIONS
# =========================================================

@app.route("/transaction-report-5-months")
def transaction_report_5_months():

    end_date = datetime.now().date()

    total_months = (
        end_date.year * 12
        + end_date.month
        - 1
    )

    total_months -= 5

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

        report_title="5 Months Transactions"
    )
# =========================================================
# LEDGER STATEMENT
# =========================================================

@app.route("/ledger")
def ledger():

    account_number = request.args.get(
        "account_number",
        ""
    ).strip()

    from_date = request.args.get(
        "from_date",
        ""
    ).strip()

    to_date = request.args.get(
        "to_date",
        ""
    ).strip()

    conn = get_db()

    customers = conn.execute("""
        SELECT
            id,
            account_number,
            name,
            mobile,
            bank_name,
            ifsc_code
        FROM customers
        ORDER BY name ASC
    """).fetchall()

    customer = None
    transactions = []

    total_amount = 0.0

    # -----------------------------------------------------
    # CUSTOMER SELECT
    # -----------------------------------------------------

    if account_number:

        customer = conn.execute("""
            SELECT
                id,
                account_number,
                name,
                mobile,
                bank_name,
                ifsc_code
            FROM customers
            WHERE account_number = ?
        """, (
            account_number,
        )).fetchone()

    # -----------------------------------------------------
    # TRANSACTIONS
    # -----------------------------------------------------

    if customer:

        query = """
            SELECT
                id,
                batch_id,
                amount,
                transaction_datetime,
                utr_number,
                site
            FROM transactions
            WHERE customer_id = ?
        """

        params = [
            customer["id"]
        ]

        # From Date
        if from_date:

            query += """
                AND substr(
                    transaction_datetime,
                    1,
                    10
                ) >= ?
            """

            params.append(
                from_date
            )

        # To Date
        if to_date:

            query += """
                AND substr(
                    transaction_datetime,
                    1,
                    10
                ) <= ?
            """

            params.append(
                to_date
            )

        query += """
            ORDER BY
                transaction_datetime ASC,
                id ASC
        """

        transactions = conn.execute(
            query,
            params
        ).fetchall()

        total_amount = sum(
            float(row["amount"] or 0)
            for row in transactions
        )

    conn.close()

    return render_template(
        "ledger.html",

        customers=customers,

        customer=customer,

        transactions=transactions,

        account_number=account_number,

        from_date=from_date,

        to_date=to_date,

        total_amount=total_amount
    )
# =========================================================
# START APP
# =========================================================

create_database()
add_missing_columns()


if __name__ == "__main__":

    app.run(
        debug=True
    )