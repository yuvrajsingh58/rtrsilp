from flask import Flask, render_template, render_template_string, request, redirect, url_for
import sqlite3
from datetime import datetime, date
import re

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
    # YYYY-MM-DD EXTRA
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
# HOME
# =========================================================

@app.route("/")
def home():

    customers = get_all_customers()

    total_accounts = len(customers)

    capital = get_capital()

    # Today's Transaction Amount
    today = datetime.now().strftime("%Y-%m-%d")

    today_transactions = get_transactions_by_date(today)

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
#
# IMPORTANT FIX:
# पहले /capital पर capital.html missing होने से
# TemplateNotFound आ रहा था.
#
# अब capital page सीधे app.py से खुलेगा.
#
# साथ ही /set-capital भी बनाया गया है ताकि पुराने
# button/form से 404 न आए.
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

            return """
            <h2 style="font-family:Arial;color:red;">
                Invalid Capital Amount
            </h2>
            <p>Please enter a valid number.</p>
            <a href="/capital">Back</a>
            """

        set_capital(amount)

        return redirect(
            url_for("capital_page")
        )

    capital = get_capital()

    return render_template_string("""
<!DOCTYPE html>
<html lang="en">

<head>

    <meta charset="UTF-8">

    <meta name="viewport"
          content="width=device-width, initial-scale=1.0">

    <title>Capital</title>

    <style>

        * {
            box-sizing: border-box;
        }

        body {
            margin: 0;
            padding: 30px;
            background: #f4f6f8;
            font-family: Arial, sans-serif;
        }

        .container {
            max-width: 600px;
            margin: 50px auto;
            background: white;
            padding: 30px;
            border-radius: 15px;
            box-shadow: 0 5px 20px rgba(0,0,0,0.10);
        }

        h1 {
            text-align: center;
            margin-bottom: 25px;
        }

        .current {
            background: #f1f5f9;
            padding: 18px;
            border-radius: 10px;
            text-align: center;
            margin-bottom: 25px;
        }

        .current strong {
            font-size: 28px;
        }

        label {
            display: block;
            font-weight: bold;
            margin-bottom: 8px;
        }

        input {
            width: 100%;
            padding: 13px;
            font-size: 18px;
            border: 1px solid #ccc;
            border-radius: 8px;
            margin-bottom: 18px;
        }

        button {
            width: 100%;
            padding: 14px;
            border: none;
            border-radius: 8px;
            background: #198754;
            color: white;
            font-size: 18px;
            font-weight: bold;
            cursor: pointer;
        }

        button:hover {
            background: #157347;
        }

        .back {
            display: block;
            text-align: center;
            margin-top: 18px;
            text-decoration: none;
            color: #333;
        }

    </style>

</head>

<body>

    <div class="container">

        <h1>Capital Management</h1>

        <div class="current">

            Current Capital

            <br>

            <strong>
                ₹{{ "%.2f"|format(capital) }}
            </strong>

        </div>

        <form method="POST"
              action="{{ url_for('capital_page') }}">

            <label>
                Enter Capital Amount
            </label>

            <input
                type="number"
                name="capital"
                step="0.01"
                min="0"
                value="{{ capital }}"
                required
            >

            <button type="submit">
                Save Capital
            </button>

        </form>

        <hr style="margin:25px 0;border:0;border-top:1px solid #ddd;">

        <form method="POST"
              action="{{ url_for('add_capital') }}">

            <label>
                Add Capital
            </label>

            <input
                type="number"
                name="add_capital"
                step="0.01"
                min="0.01"
                placeholder="Enter amount to add"
                required
            >

            <button type="submit" style="background:#0d6efd;">
                + Add Capital
            </button>

        </form>

        <a class="back"
           href="/">
            ← Back to Home
        </a>

    </div>

</body>

</html>
    """, capital=capital)


# =========================================================
# ADD CAPITAL
# =========================================================
#
# Current capital ko badhane ke liye.
# Example: Current Capital ₹5,000 + Add ₹2,000 = ₹7,000
# =========================================================

@app.route(
    "/add-capital",
    methods=["POST"]
)
def add_capital():

    amount_text = request.form.get(
        "add_capital",
        ""
    ).strip()

    try:
        amount = float(
            amount_text or 0
        )
    except ValueError:
        return """
        <h2 style="font-family:Arial;color:red;">
            Invalid Capital Amount
        </h2>
        <p>Please enter a valid number.</p>
        <a href="/capital">Back</a>
        """

    if amount <= 0:
        return """
        <h2 style="font-family:Arial;color:red;">
            Invalid Capital Amount
        </h2>
        <p>Please enter an amount greater than 0.</p>
        <a href="/capital">Back</a>
        """

    change_capital(amount)

    return redirect(
        url_for("capital_page")
    )



# =========================================================
# CAPITAL ALIAS
# =========================================================
#
# अगर आपके index.html / पुराने capital button में
# /set-capital लगा हुआ है तो अब 404 नहीं आएगा.
#
# GET  -> Capital page
# POST -> Capital save
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

        return "Account Number and Name are required."

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

        return "This Account Number already exists."

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
        """, (f"%{name}%",)).fetchall()

    elif mobile:

        customers = conn.execute("""
            SELECT *
            FROM customers
            WHERE mobile LIKE ?
            ORDER BY name
        """, (f"%{mobile}%",)).fetchall()

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

    # Today's Transaction Amount
    today = datetime.now().strftime("%Y-%m-%d")

    today_transactions = get_transactions_by_date(today)

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

    transaction_count = len(transactions)

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
# PROFILE
# =========================================================

@app.route("/profile/<int:id>")
def profile(id):

    customer = get_customer(id)

    if customer is None:
        return "Account not found."

    history = get_transaction_history(id)

    total_amount = get_total_transaction_amount(id)

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

        return "Transaction save karte time error aa gaya."

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

        return "Transaction history not found."

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

        return "Transaction history not found."

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

    difference = new_total - old_batch_total

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

        new_capital = current_capital - difference

    elif difference < 0:

        new_capital = current_capital + abs(difference)

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

        return "Transaction history not found."

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

        return "Account Number and Name are required."

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

        return "This Account Number already exists."

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
# START APP
# =========================================================

create_database()
add_missing_columns()


if __name__ == "__main__":

    app.run(
        debug=True
    )