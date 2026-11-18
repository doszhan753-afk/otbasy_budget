"""Отбасы бюджеті — үй шаруашылығының қаржысын есепке алу веб-бағдарламасы.

Flask + SQLite. Іске қосу:  python app.py
"""
import csv
import io
import math
import os
import re
import secrets
import sqlite3
from datetime import date, datetime
from functools import wraps

from flask import (Blueprint, Flask, Response, abort, current_app, flash, g,
                   redirect, render_template, request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

MONTHS_KK = ["Қаңтар", "Ақпан", "Наурыз", "Сәуір", "Мамыр", "Маусым",
             "Шілде", "Тамыз", "Қыркүйек", "Қазан", "Қараша", "Желтоқсан"]
KIND_LABEL = {"income": "Кіріс", "expense": "Шығыс"}
PAGE_SIZE = 15
MAX_AMOUNT = 10 ** 12
PALETTE = ["#1f7a63", "#e3a72f", "#d4573b", "#3b6fb6", "#8a5fbf",
           "#2aa5a0", "#c9769b", "#8c8c7a"]

DEFAULT_CATEGORIES = [
    ("Азық-түлік", "expense"), ("Пәтер ақысы", "expense"),
    ("Коммуналдық төлемдер", "expense"), ("Көлік", "expense"),
    ("Байланыс және интернет", "expense"), ("Денсаулық", "expense"),
    ("Білім", "expense"), ("Киім", "expense"),
    ("Демалыс және ойын-сауық", "expense"), ("Басқа шығыстар", "expense"),
    ("Жалақы", "income"), ("Қосымша табыс", "income"),
    ("Сыйлық", "income"), ("Басқа кірістер", "income"),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    family_name   TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS members (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name    TEXT NOT NULL,
    UNIQUE (user_id, name)
);
CREATE TABLE IF NOT EXISTS categories (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name    TEXT NOT NULL,
    kind    TEXT NOT NULL CHECK (kind IN ('income', 'expense')),
    UNIQUE (user_id, name, kind)
);
CREATE TABLE IF NOT EXISTS transactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE RESTRICT,
    member_id   INTEGER REFERENCES members(id) ON DELETE SET NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('income', 'expense')),
    amount      INTEGER NOT NULL CHECK (amount > 0),
    tx_date     TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_tx_user_date ON transactions(user_id, tx_date);
CREATE TABLE IF NOT EXISTS budgets (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    category_id  INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    month        TEXT NOT NULL,
    limit_amount INTEGER NOT NULL CHECK (limit_amount > 0),
    UNIQUE (category_id, month)
);
"""

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,30}$")
MONTH_RE = re.compile(r"^20\d{2}-(0[1-9]|1[0-2])$")

bp = Blueprint("main", __name__)


# --------------------------------------------------------------------------
# Дерекқор
# --------------------------------------------------------------------------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.executescript(SCHEMA)
    db.commit()


def seed_user_defaults(db, user_id):
    db.executemany(
        "INSERT INTO categories(user_id, name, kind) VALUES (?, ?, ?)",
        [(user_id, name, kind) for name, kind in DEFAULT_CATEGORIES])


# --------------------------------------------------------------------------
# Көмекші функциялар
# --------------------------------------------------------------------------
def load_secret_key():
    env = os.environ.get("SECRET_KEY")
    if env:
        return env
    path = os.path.join(BASE_DIR, ".secret_key")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    key = secrets.token_hex(32)
    with open(path, "w", encoding="utf-8") as f:
        f.write(key)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(16)
    return session["csrf_token"]


def current_month():
    return date.today().strftime("%Y-%m")


def parse_month(value):
    return value if value and MONTH_RE.match(value) else current_month()


def month_range(month):
    y, m = int(month[:4]), int(month[5:])
    start = date(y, m, 1)
    end = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
    return start.isoformat(), end.isoformat()


def shift_month(month, delta):
    y, m = int(month[:4]), int(month[5:])
    idx = y * 12 + (m - 1) + delta
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def month_label(month):
    return f"{MONTHS_KK[int(month[5:]) - 1]} {month[:4]}"


def fmt_money(value):
    return f"{int(value or 0):,}".replace(",", " ") + " ₸"


def fmt_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return value


def parse_amount(raw):
    cleaned = (raw or "").replace(" ", "").replace(" ", "")
    if not re.fullmatch(r"[0-9]{1,13}", cleaned):
        return None
    value = int(cleaned)
    return value if 0 < value <= MAX_AMOUNT else None


def parse_date(raw):
    try:
        d = datetime.strptime((raw or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None
    return d if 2000 <= d.year <= 2100 else None


def like_escape(text):
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def csv_safe(text):
    """CSV-инъекциядан қорғау (Excel формулалары)."""
    text = text or ""
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def chart_items(rows, top=7):
    """Шығыстар құрылымы үшін: ең үлкен санаттар + 'Басқалар', дөңгелек диаграмма градиенті."""
    rows = [{"name": r["name"], "total": r["total"]} for r in rows]
    if len(rows) > top:
        rest = sum(r["total"] for r in rows[top:])
        rows = rows[:top] + [{"name": "Басқалар", "total": rest}]
    total = sum(r["total"] for r in rows)
    stops, acc = [], 0
    for i, r in enumerate(rows):
        r["color"] = PALETTE[i % len(PALETTE)]
        r["percent"] = round(r["total"] * 100 / total, 1) if total else 0
        start = acc * 100 / total if total else 0
        acc += r["total"]
        end = acc * 100 / total if total else 0
        stops.append(f"{r['color']} {start:.2f}% {end:.2f}%")
    gradient = "conic-gradient(" + ", ".join(stops) + ")" if stops else "none"
    return rows, gradient


def month_totals(db, uid, month):
    start, end = month_range(month)
    rows = db.execute(
        "SELECT kind, COALESCE(SUM(amount), 0) AS total FROM transactions "
        "WHERE user_id = ? AND tx_date >= ? AND tx_date < ? GROUP BY kind",
        (uid, start, end)).fetchall()
    totals = {"income": 0, "expense": 0}
    for r in rows:
        totals[r["kind"]] = r["total"]
    totals["balance"] = totals["income"] - totals["expense"]
    return totals


def category_totals(db, uid, month, kind):
    start, end = month_range(month)
    return db.execute(
        "SELECT c.id, c.name, SUM(t.amount) AS total FROM transactions t "
        "JOIN categories c ON c.id = t.category_id "
        "WHERE t.user_id = ? AND t.kind = ? AND t.tx_date >= ? AND t.tx_date < ? "
        "GROUP BY c.id ORDER BY total DESC", (uid, kind, start, end)).fetchall()


def trend(db, uid, month, count=6):
    months = [shift_month(month, -i) for i in range(count - 1, -1, -1)]
    start, _ = month_range(months[0])
    _, end = month_range(months[-1])
    rows = db.execute(
        "SELECT substr(tx_date, 1, 7) AS m, kind, SUM(amount) AS total FROM transactions "
        "WHERE user_id = ? AND tx_date >= ? AND tx_date < ? GROUP BY m, kind",
        (uid, start, end)).fetchall()
    data = {m: {"income": 0, "expense": 0} for m in months}
    for r in rows:
        data[r["m"]][r["kind"]] = r["total"]
    peak = max([max(v["income"], v["expense"]) for v in data.values()] + [1])
    return [{"month": m, "short": MONTHS_KK[int(m[5:]) - 1][:3],
             "income": v["income"], "expense": v["expense"],
             "income_h": round(v["income"] * 100 / peak),
             "expense_h": round(v["expense"] * 100 / peak)}
            for m, v in data.items()]


def budget_progress(db, uid, month, only_with_limit=False):
    start, end = month_range(month)
    rows = db.execute(
        "SELECT c.id, c.name, COALESCE(b.limit_amount, 0) AS limit_amount, "
        "COALESCE((SELECT SUM(t.amount) FROM transactions t WHERE t.category_id = c.id "
        "          AND t.tx_date >= ? AND t.tx_date < ?), 0) AS spent "
        "FROM categories c LEFT JOIN budgets b ON b.category_id = c.id AND b.month = ? "
        "WHERE c.user_id = ? AND c.kind = 'expense' ORDER BY c.name",
        (start, end, month, uid)).fetchall()
    result = []
    for r in rows:
        item = dict(r)
        limit, spent = item["limit_amount"], item["spent"]
        if only_with_limit and limit <= 0:
            continue
        raw = spent * 100 / limit if limit > 0 else 0
        item["percent"] = min(round(raw), 100)
        item["raw_percent"] = round(raw)
        item["left"] = limit - spent
        item["status"] = ("over" if limit > 0 and spent > limit
                          else "warn" if limit > 0 and raw >= 80 else "ok")
        result.append(item)
    return result


def user_lists(db, uid):
    categories = db.execute(
        "SELECT id, name, kind FROM categories WHERE user_id = ? ORDER BY kind DESC, name",
        (uid,)).fetchall()
    members = db.execute(
        "SELECT id, name FROM members WHERE user_id = ? ORDER BY name", (uid,)).fetchall()
    return categories, members


# --------------------------------------------------------------------------
# Сұраныс алдындағы тексерулер, шаблон контексті
# --------------------------------------------------------------------------
@bp.before_app_request
def load_user_and_check_csrf():
    g.user = None
    uid = session.get("user_id")
    if uid is not None:
        g.user = get_db().execute(
            "SELECT id, username, family_name FROM users WHERE id = ?", (uid,)).fetchone()
        if g.user is None:
            session.clear()
    if request.method == "POST":
        expected = session.get("csrf_token", "")
        given = request.form.get("csrf_token", "")
        if not expected or not secrets.compare_digest(expected.encode(), given.encode()):
            abort(400)


@bp.app_context_processor
def inject_helpers():
    return {"csrf_token": csrf_token, "KIND_LABEL": KIND_LABEL,
            "month_label": month_label, "shift_month": shift_month,
            "current_month": current_month, "today": lambda: date.today().isoformat()}


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("main.login"))
        return view(*args, **kwargs)
    return wrapped


# --------------------------------------------------------------------------
# Аутентификация
# --------------------------------------------------------------------------
@bp.route("/")
def index():
    return redirect(url_for("main.dashboard" if g.user else "main.login"))


@bp.route("/register", methods=("GET", "POST"))
def register():
    if g.user:
        return redirect(url_for("main.dashboard"))
    form = {}
    if request.method == "POST":
        form = request.form
        username = form.get("username", "").strip()
        family = form.get("family_name", "").strip()
        pw, pw2 = form.get("password", ""), form.get("password2", "")
        errors = []
        if not USERNAME_RE.match(username):
            errors.append("Логин 3–30 таңба: латын әріптері, сандар, нүкте, сызықша немесе астын сызу.")
        if not 2 <= len(family) <= 60:
            errors.append("Отбасы атауы 2–60 таңба болуы керек.")
        if not 6 <= len(pw) <= 128:
            errors.append("Құпиясөз 6–128 таңба болуы керек.")
        elif pw != pw2:
            errors.append("Құпиясөздер сәйкес келмейді.")
        if not errors:
            db = get_db()
            try:
                cur = db.execute(
                    "INSERT INTO users(username, password_hash, family_name) VALUES (?, ?, ?)",
                    (username.lower(), generate_password_hash(pw), family))
            except sqlite3.IntegrityError:
                errors.append("Бұл логин бос емес, басқасын таңдаңыз.")
            else:
                seed_user_defaults(db, cur.lastrowid)
                db.commit()
                session.clear()
                session["user_id"] = cur.lastrowid
                flash("Тіркелу сәтті өтті. Бастапқы санаттар қосылды.", "success")
                return redirect(url_for("main.dashboard"))
        for e in errors:
            flash(e, "error")
    return render_template("register.html", form=form)


@bp.route("/login", methods=("GET", "POST"))
def login():
    if g.user:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        pw = request.form.get("password", "")
        user = get_db().execute(
            "SELECT id, password_hash FROM users WHERE username = ?", (username,)).fetchone()
        if user is None or not check_password_hash(user["password_hash"], pw):
            flash("Логин немесе құпиясөз қате.", "error")
        else:
            session.clear()
            session["user_id"] = user["id"]
            return redirect(url_for("main.dashboard"))
    return render_template("login.html")


@bp.route("/logout", methods=("POST",))
def logout():
    session.clear()
    return redirect(url_for("main.login"))


# --------------------------------------------------------------------------
# Шолу (dashboard)
# --------------------------------------------------------------------------
@bp.route("/dashboard")
@login_required
def dashboard():
    db, uid = get_db(), g.user["id"]
    month = parse_month(request.args.get("month"))
    totals = month_totals(db, uid, month)
    items, gradient = chart_items(category_totals(db, uid, month, "expense"))
    start, end = month_range(month)
    recent = db.execute(
        "SELECT t.*, c.name AS category FROM transactions t "
        "JOIN categories c ON c.id = t.category_id "
        "WHERE t.user_id = ? AND t.tx_date >= ? AND t.tx_date < ? "
        "ORDER BY t.tx_date DESC, t.id DESC LIMIT 6", (uid, start, end)).fetchall()
    return render_template(
        "dashboard.html", month=month, totals=totals, items=items, gradient=gradient,
        recent=recent, trend=trend(db, uid, month),
        limits=budget_progress(db, uid, month, only_with_limit=True))


# --------------------------------------------------------------------------
# Операциялар
# --------------------------------------------------------------------------
def read_transaction_form(db, uid):
    """Форманы оқып, тексереді. (data, errors) қайтарады."""
    f = request.form
    errors = []
    kind = f.get("kind", "")
    if kind not in KIND_LABEL:
        errors.append("Операция түрін таңдаңыз.")
    amount = parse_amount(f.get("amount"))
    if amount is None:
        errors.append("Сома оң бүтін сан болуы керек (мысалы, 15000).")
    tx_date = parse_date(f.get("tx_date"))
    if tx_date is None:
        errors.append("Күнді дұрыс енгізіңіз.")
    category = None
    category_id = f.get("category_id", type=int)
    if category_id is not None:
        category = db.execute(
            "SELECT id, kind FROM categories WHERE id = ? AND user_id = ?",
            (category_id, uid)).fetchone()
    if category is None:
        errors.append("Санатты таңдаңыз.")
    elif kind in KIND_LABEL and category["kind"] != kind:
        errors.append("Санат операция түріне сәйкес келмейді.")
    member_id = f.get("member_id", type=int)
    if member_id is not None:
        member = db.execute("SELECT id FROM members WHERE id = ? AND user_id = ?",
                            (member_id, uid)).fetchone()
        if member is None:
            errors.append("Отбасы мүшесі табылмады.")
    note = f.get("note", "").strip()
    if len(note) > 200:
        errors.append("Түсініктеме 200 таңбадан аспауы керек.")
    data = {"kind": kind, "amount": amount, "tx_date": tx_date.isoformat() if tx_date else "",
            "category_id": category_id, "member_id": member_id, "note": note}
    return data, errors


@bp.route("/transactions")
@login_required
def transactions():
    db, uid = get_db(), g.user["id"]
    raw_month = request.args.get("month", "")
    month = "all" if raw_month == "all" else parse_month(raw_month)
    kind = request.args.get("kind", "")
    kind = kind if kind in KIND_LABEL else ""
    category = request.args.get("category", type=int)
    member = request.args.get("member", type=int)
    q = request.args.get("q", "").strip()[:100]

    where, params = ["t.user_id = ?"], [uid]
    if month != "all":
        start, end = month_range(month)
        where += ["t.tx_date >= ?", "t.tx_date < ?"]
        params += [start, end]
    if kind:
        where.append("t.kind = ?")
        params.append(kind)
    if category:
        where.append("t.category_id = ?")
        params.append(category)
    if member:
        where.append("t.member_id = ?")
        params.append(member)
    if q:
        where.append("t.note LIKE ? ESCAPE '\\'")
        params.append(f"%{like_escape(q)}%")
    where_sql = " AND ".join(where)

    count, sum_in, sum_out = db.execute(
        "SELECT COUNT(*), COALESCE(SUM(CASE WHEN t.kind='income' THEN t.amount END), 0), "
        "COALESCE(SUM(CASE WHEN t.kind='expense' THEN t.amount END), 0) "
        f"FROM transactions t WHERE {where_sql}", params).fetchone()
    pages = max(math.ceil(count / PAGE_SIZE), 1)
    page = min(max(request.args.get("page", 1, type=int), 1), pages)
    rows = db.execute(
        "SELECT t.*, c.name AS category, m.name AS member FROM transactions t "
        "JOIN categories c ON c.id = t.category_id "
        "LEFT JOIN members m ON m.id = t.member_id "
        f"WHERE {where_sql} ORDER BY t.tx_date DESC, t.id DESC LIMIT ? OFFSET ?",
        params + [PAGE_SIZE, (page - 1) * PAGE_SIZE]).fetchall()

    categories, members = user_lists(db, uid)
    args = {k: v for k, v in {"month": month, "kind": kind, "category": category,
                              "member": member, "q": q}.items() if v}
    return render_template(
        "transactions.html", rows=rows, month=month, kind=kind, category=category,
        member=member, q=q, page=page, pages=pages, count=count, args=args,
        sum_in=sum_in, sum_out=sum_out, categories=categories, members=members)


@bp.route("/transactions/new", methods=("GET", "POST"))
@login_required
def transaction_new():
    db, uid = get_db(), g.user["id"]
    categories, members = user_lists(db, uid)
    if request.method == "POST":
        data, errors = read_transaction_form(db, uid)
        if not errors:
            db.execute(
                "INSERT INTO transactions(user_id, category_id, member_id, kind, amount, tx_date, note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (uid, data["category_id"], data["member_id"], data["kind"],
                 data["amount"], data["tx_date"], data["note"]))
            db.commit()
            flash("Операция сақталды.", "success")
            return redirect(url_for("main.transactions", month=data["tx_date"][:7]))
        for e in errors:
            flash(e, "error")
        tx = request.form
    else:
        kind = request.args.get("kind")
        tx = {"kind": kind if kind in KIND_LABEL else "expense",
              "tx_date": date.today().isoformat()}
    return render_template("transaction_form.html", tx=tx, categories=categories,
                           members=members, editing=False)


@bp.route("/transactions/<int:tx_id>/edit", methods=("GET", "POST"))
@login_required
def transaction_edit(tx_id):
    db, uid = get_db(), g.user["id"]
    row = db.execute("SELECT * FROM transactions WHERE id = ? AND user_id = ?",
                     (tx_id, uid)).fetchone()
    if row is None:
        abort(404)
    categories, members = user_lists(db, uid)
    tx = row
    if request.method == "POST":
        data, errors = read_transaction_form(db, uid)
        if not errors:
            db.execute(
                "UPDATE transactions SET category_id = ?, member_id = ?, kind = ?, amount = ?, "
                "tx_date = ?, note = ? WHERE id = ? AND user_id = ?",
                (data["category_id"], data["member_id"], data["kind"], data["amount"],
                 data["tx_date"], data["note"], tx_id, uid))
            db.commit()
            flash("Өзгерістер сақталды.", "success")
            return redirect(url_for("main.transactions", month=data["tx_date"][:7]))
        for e in errors:
            flash(e, "error")
        tx = request.form
    return render_template("transaction_form.html", tx=tx, categories=categories,
                           members=members, editing=True, tx_id=tx_id)


@bp.route("/transactions/<int:tx_id>/delete", methods=("POST",))
@login_required
def transaction_delete(tx_id):
    db = get_db()
    cur = db.execute("DELETE FROM transactions WHERE id = ? AND user_id = ?",
                     (tx_id, g.user["id"]))
    db.commit()
    if cur.rowcount:
        flash("Операция жойылды.", "success")
    back = request.form.get("back", "")
    # Тек жергілікті жолға қайта бағыттаймыз (open redirect-тен қорғану)
    if not back.startswith("/") or back.startswith("//") or "\\" in back:
        back = url_for("main.transactions")
    return redirect(back)


# --------------------------------------------------------------------------
# Санаттар
# --------------------------------------------------------------------------
@bp.route("/categories", methods=("GET", "POST"))
@login_required
def categories():
    db, uid = get_db(), g.user["id"]
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        kind = request.form.get("kind", "")
        if kind not in KIND_LABEL or not 2 <= len(name) <= 40:
            flash("Санат атауы 2–40 таңба болуы және түрі таңдалуы керек.", "error")
        else:
            try:
                db.execute("INSERT INTO categories(user_id, name, kind) VALUES (?, ?, ?)",
                           (uid, name, kind))
                db.commit()
                flash("Санат қосылды.", "success")
            except sqlite3.IntegrityError:
                flash("Мұндай санат бұрын қосылған.", "error")
        return redirect(url_for("main.categories"))
    rows = db.execute(
        "SELECT c.id, c.name, c.kind, COUNT(t.id) AS uses FROM categories c "
        "LEFT JOIN transactions t ON t.category_id = c.id "
        "WHERE c.user_id = ? GROUP BY c.id ORDER BY c.name", (uid,)).fetchall()
    return render_template("categories.html",
                           expense=[r for r in rows if r["kind"] == "expense"],
                           income=[r for r in rows if r["kind"] == "income"])


@bp.route("/categories/<int:cat_id>/delete", methods=("POST",))
@login_required
def category_delete(cat_id):
    db, uid = get_db(), g.user["id"]
    cat = db.execute("SELECT id FROM categories WHERE id = ? AND user_id = ?",
                     (cat_id, uid)).fetchone()
    if cat is None:
        abort(404)
    used = db.execute("SELECT COUNT(*) FROM transactions WHERE category_id = ?",
                      (cat_id,)).fetchone()[0]
    if used:
        flash("Бұл санатта операциялар бар, сондықтан жоюға болмайды.", "error")
    else:
        db.execute("DELETE FROM categories WHERE id = ?", (cat_id,))
        db.commit()
        flash("Санат жойылды.", "success")
    return redirect(url_for("main.categories"))


# --------------------------------------------------------------------------
# Отбасы мүшелері
# --------------------------------------------------------------------------
@bp.route("/members", methods=("GET", "POST"))
@login_required
def members():
    db, uid = get_db(), g.user["id"]
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not 2 <= len(name) <= 40:
            flash("Аты 2–40 таңба болуы керек.", "error")
        else:
            try:
                db.execute("INSERT INTO members(user_id, name) VALUES (?, ?)", (uid, name))
                db.commit()
                flash("Отбасы мүшесі қосылды.", "success")
            except sqlite3.IntegrityError:
                flash("Мұндай мүше бұрын қосылған.", "error")
        return redirect(url_for("main.members"))
    rows = db.execute(
        "SELECT m.id, m.name, COUNT(t.id) AS uses, COALESCE(SUM(CASE WHEN t.kind='expense' THEN t.amount END), 0) AS spent "
        "FROM members m LEFT JOIN transactions t ON t.member_id = m.id "
        "WHERE m.user_id = ? GROUP BY m.id ORDER BY m.name", (uid,)).fetchall()
    return render_template("members.html", rows=rows)


@bp.route("/members/<int:member_id>/delete", methods=("POST",))
@login_required
def member_delete(member_id):
    db = get_db()
    cur = db.execute("DELETE FROM members WHERE id = ? AND user_id = ?",
                     (member_id, g.user["id"]))
    db.commit()
    if cur.rowcount:
        flash("Отбасы мүшесі жойылды (операциялар сақталды).", "success")
    return redirect(url_for("main.members"))


# --------------------------------------------------------------------------
# Айлық лимиттер
# --------------------------------------------------------------------------
@bp.route("/budgets", methods=("GET", "POST"))
@login_required
def budgets():
    db, uid = get_db(), g.user["id"]
    if request.method == "POST":
        month = parse_month(request.form.get("month"))
        cat_id = request.form.get("category_id", type=int)
        cat = db.execute(
            "SELECT id FROM categories WHERE id = ? AND user_id = ? AND kind = 'expense'",
            (cat_id, uid)).fetchone() if cat_id else None
        raw = request.form.get("limit", "").strip()
        if cat is None:
            flash("Санат табылмады.", "error")
        elif raw in ("", "0"):
            db.execute("DELETE FROM budgets WHERE category_id = ? AND month = ?", (cat_id, month))
            db.commit()
            flash("Лимит өшірілді.", "success")
        else:
            limit = parse_amount(raw)
            if limit is None:
                flash("Лимит оң бүтін сан болуы керек.", "error")
            else:
                db.execute(
                    "INSERT INTO budgets(user_id, category_id, month, limit_amount) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(category_id, month) DO UPDATE SET limit_amount = excluded.limit_amount",
                    (uid, cat_id, month, limit))
                db.commit()
                flash("Лимит сақталды.", "success")
        return redirect(url_for("main.budgets", month=month))
    month = parse_month(request.args.get("month"))
    return render_template("budgets.html", month=month, rows=budget_progress(db, uid, month))


@bp.route("/budgets/copy", methods=("POST",))
@login_required
def budgets_copy():
    db, uid = get_db(), g.user["id"]
    month = parse_month(request.form.get("month"))
    prev = shift_month(month, -1)
    cur = db.execute(
        "INSERT OR IGNORE INTO budgets(user_id, category_id, month, limit_amount) "
        "SELECT user_id, category_id, ?, limit_amount FROM budgets WHERE user_id = ? AND month = ?",
        (month, uid, prev))
    db.commit()
    flash(f"Өткен айдан {cur.rowcount} лимит көшірілді." if cur.rowcount
          else "Көшіретін жаңа лимит жоқ.", "success" if cur.rowcount else "error")
    return redirect(url_for("main.budgets", month=month))


# --------------------------------------------------------------------------
# Есептер
# --------------------------------------------------------------------------
@bp.route("/reports")
@login_required
def reports():
    db, uid = get_db(), g.user["id"]
    month = parse_month(request.args.get("month"))
    start, end = month_range(month)
    totals = month_totals(db, uid, month)
    expense_rows = category_totals(db, uid, month, "expense")
    income_rows = category_totals(db, uid, month, "income")
    by_member = db.execute(
        "SELECT COALESCE(m.name, 'Көрсетілмеген') AS name, "
        "COALESCE(SUM(CASE WHEN t.kind='expense' THEN t.amount END), 0) AS spent, "
        "COALESCE(SUM(CASE WHEN t.kind='income' THEN t.amount END), 0) AS earned "
        "FROM transactions t LEFT JOIN members m ON m.id = t.member_id "
        "WHERE t.user_id = ? AND t.tx_date >= ? AND t.tx_date < ? "
        "GROUP BY m.id ORDER BY spent DESC", (uid, start, end)).fetchall()
    top = db.execute(
        "SELECT t.*, c.name AS category FROM transactions t JOIN categories c ON c.id = t.category_id "
        "WHERE t.user_id = ? AND t.kind = 'expense' AND t.tx_date >= ? AND t.tx_date < ? "
        "ORDER BY t.amount DESC LIMIT 5", (uid, start, end)).fetchall()
    days = (date.fromisoformat(end) - date.fromisoformat(start)).days
    if month == current_month():
        days = date.today().day  # ағымдағы айда тек өткен күндер ескеріледі
    savings_rate = round(totals["balance"] * 100 / totals["income"]) if totals["income"] else None
    return render_template(
        "reports.html", month=month, totals=totals, expense_rows=expense_rows,
        income_rows=income_rows, by_member=by_member, top=top, trend=trend(db, uid, month),
        avg_day=round(totals["expense"] / days), savings_rate=savings_rate)


@bp.route("/reports/export.csv")
@login_required
def export_csv():
    db, uid = get_db(), g.user["id"]
    month = parse_month(request.args.get("month"))
    start, end = month_range(month)
    rows = db.execute(
        "SELECT t.tx_date, t.kind, c.name AS category, m.name AS member, t.amount, t.note "
        "FROM transactions t JOIN categories c ON c.id = t.category_id "
        "LEFT JOIN members m ON m.id = t.member_id "
        "WHERE t.user_id = ? AND t.tx_date >= ? AND t.tx_date < ? "
        "ORDER BY t.tx_date, t.id", (uid, start, end)).fetchall()
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(["Күні", "Түрі", "Санат", "Мүше", "Сома (₸)", "Түсініктеме"])
    for r in rows:
        writer.writerow([fmt_date(r["tx_date"]), KIND_LABEL[r["kind"]], csv_safe(r["category"]),
                         csv_safe(r["member"] or ""), r["amount"], csv_safe(r["note"])])
    body = "﻿" + buf.getvalue()  # BOM — Excel кирилл/қазақ әріптерін дұрыс ашуы үшін
    return Response(body, mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename=budget_{month}.csv"})


# --------------------------------------------------------------------------
# Қателер
# --------------------------------------------------------------------------
@bp.app_errorhandler(400)
def bad_request(_e):
    return render_template("error.html", code=400,
                           message="Сұрау жарамсыз немесе сессия мерзімі өтті. Бетті жаңартып көріңіз."), 400


@bp.app_errorhandler(404)
def not_found(_e):
    return render_template("error.html", code=404, message="Бет табылмады."), 404


# --------------------------------------------------------------------------
# Қосымшаны жасау
# --------------------------------------------------------------------------
def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(
        DATABASE=os.environ.get("DATABASE_PATH") or os.path.join(BASE_DIR, "budget.db"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        MAX_CONTENT_LENGTH=1024 * 1024,
    )
    if test_config:
        app.config.update(test_config)
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = load_secret_key()

    app.jinja_env.filters["money"] = fmt_money
    app.jinja_env.filters["kzdate"] = fmt_date
    app.teardown_appcontext(close_db)
    app.register_blueprint(bp)

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "img-src 'self' data:; form-action 'self'; frame-ancestors 'none'")
        return resp

    with app.app_context():
        init_db()
    return app


if __name__ == "__main__":
    create_app().run(debug=os.environ.get("FLASK_DEBUG") == "1")
