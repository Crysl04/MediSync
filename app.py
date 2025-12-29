import os
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
import psycopg2
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from functools import wraps

DATABASE_URL = os.environ.get("DATABASE_URL")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev_secret_key")


# =========================
# DATABASE INITIALIZATION
# =========================
def init_db():
    conn = psycopg2.connect(DATABASE_URL)
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        full_name TEXT NOT NULL,
        role TEXT DEFAULT 'staff',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS user_activity (
        id SERIAL PRIMARY KEY,
        username TEXT NOT NULL,
        activity TEXT NOT NULL,
        activity_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS Category (
        id SERIAL PRIMARY KEY,
        category_name TEXT NOT NULL,
        created_at DATE DEFAULT CURRENT_DATE
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS Product (
        id SERIAL PRIMARY KEY,
        product_name TEXT NOT NULL,
        product_type TEXT CHECK (product_type IN ('medicine','supply')) NOT NULL,
        category_id INTEGER REFERENCES Category(id),
        stock_quantity INTEGER DEFAULT 0,
        stock_status TEXT DEFAULT 'in stock',
        status TEXT DEFAULT 'active',
        created_at DATE DEFAULT CURRENT_DATE
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS Supplier (
        id SERIAL PRIMARY KEY,
        supplier_name TEXT UNIQUE NOT NULL,
        contact_person TEXT,
        contact_number TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS Purchase (
        id SERIAL PRIMARY KEY,
        product_id INTEGER REFERENCES Product(id) ON DELETE CASCADE,
        supplier_id INTEGER REFERENCES Supplier(id),
        batch_number TEXT,
        purchase_quantity INTEGER NOT NULL,
        remaining_quantity INTEGER NOT NULL,
        expiration_date DATE,
        status TEXT DEFAULT 'in stock',
        purchase_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS "Order" (
        order_id SERIAL PRIMARY KEY,
        product_id INTEGER REFERENCES Product(id),
        batch_number TEXT,
        order_quantity INTEGER NOT NULL,
        customer TEXT NOT NULL,
        order_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS notification (
        id SERIAL PRIMARY KEY,
        product_id INTEGER,
        batch_id TEXT,
        message TEXT,
        type TEXT,
        is_read BOOLEAN DEFAULT FALSE,
        ignored BOOLEAN DEFAULT FALSE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.commit()
    conn.close()
    print("Database initialized successfully.")


# =========================
# AUTH DECORATOR
# =========================
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# =========================
# ACTIVITY LOG
# =========================
def log_activity(username, activity):
    conn = psycopg2.connect(DATABASE_URL)
    c = conn.cursor()
    c.execute(
        "INSERT INTO user_activity (username, activity) VALUES (%s, %s)",
        (username, activity),
    )
    conn.commit()
    conn.close()


# =========================
# AUTH ROUTES
# =========================
@app.route("/")
def login():
    session.clear()
    return render_template("index.html")


@app.route("/auth", methods=["POST"])
def auth():
    username = request.form["username"]
    password = request.form["password"]

    conn = psycopg2.connect(DATABASE_URL)
    c = conn.cursor()

    c.execute("SELECT id, username, password, full_name, role FROM users WHERE username=%s", (username,))
    user = c.fetchone()
    conn.close()

    if user and check_password_hash(user[2], password):
        session.update({
            "logged_in": True,
            "user_id": user[0],
            "username": user[1],
            "name": user[3],
            "role": user[4],
        })
        log_activity(username, "Logged in")
        return redirect(url_for("dashboard"))

    return render_template("index.html", error="Invalid login")


@app.route("/logout")
def logout():
    if session.get("username"):
        log_activity(session["username"], "Logged out")
    session.clear()
    return redirect(url_for("login"))


# =========================
# DASHBOARD
# =========================
@app.route("/dashboard")
@login_required
def dashboard():
    conn = psycopg2.connect(DATABASE_URL)
    c = conn.cursor()

    c.execute("SELECT SUM(stock_quantity) FROM Product WHERE status='active'")
    total_stocks = c.fetchone()[0] or 0

    c.execute("""
        SELECT
            SUM(CASE WHEN product_type='medicine' THEN 1 ELSE 0 END),
            SUM(CASE WHEN product_type='supply' THEN 1 ELSE 0 END)
        FROM Product WHERE status='active'
    """)
    meds, supplies = c.fetchone()

    conn.close()

    return render_template(
        "admin.html",
        total_stocks=total_stocks,
        medicines=meds or 0,
        supplies=supplies or 0,
    )


# =========================
# PURCHASES (CONNECTED TO SUPPLIER)
# =========================
@app.route("/purchases")
@login_required
def purchases():
    conn = psycopg2.connect(DATABASE_URL)
    c = conn.cursor()

    c.execute("""
        SELECT
            pu.id,
            pr.product_name,
            pu.batch_number,
            pu.purchase_quantity,
            pu.remaining_quantity,
            pu.expiration_date,
            pu.status,
            pu.purchase_date,
            s.supplier_name
        FROM Purchase pu
        LEFT JOIN Product pr ON pu.product_id = pr.id
        LEFT JOIN Supplier s ON pu.supplier_id = s.id
        ORDER BY pu.purchase_date DESC
    """)
    purchases = c.fetchall()

    c.execute("SELECT id, product_name FROM Product ORDER BY product_name")
    products = c.fetchall()

    c.execute("SELECT id, supplier_name FROM Supplier ORDER BY supplier_name")
    suppliers = c.fetchall()

    conn.close()

    return render_template(
        "purchase.html",
        purchases=purchases,
        products=products,
        suppliers=suppliers
    )


@app.route("/add-purchase", methods=["POST"])
@login_required
def add_purchase():
    conn = psycopg2.connect(DATABASE_URL)
    c = conn.cursor()

    product_id = request.form["product_id"]
    supplier_id = request.form["supplier_id"]
    qty = int(request.form["purchase_quantity"])
    expiration = request.form["expiration_date"]

    c.execute("""
        INSERT INTO Purchase
        (product_id, supplier_id, purchase_quantity, remaining_quantity, expiration_date)
        VALUES (%s, %s, %s, %s, %s)
    """, (product_id, supplier_id, qty, qty, expiration))

    conn.commit()
    conn.close()

    log_activity(session["username"], "Added stock-in")
    return jsonify(success=True)


# =========================
# APP START
# =========================
if __name__ == "__main__":
    init_db()
    from waitress import serve
    serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
