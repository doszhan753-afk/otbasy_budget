import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app, fmt_money, month_range, parse_amount, shift_month  # noqa: E402


@pytest.fixture()
def app(tmp_path):
    return create_app({"TESTING": True, "SECRET_KEY": "test", "DATABASE": str(tmp_path / "t.db")})


@pytest.fixture()
def client(app):
    return app.test_client()


def token(client, url="/login"):
    html = client.get(url).get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([0-9a-f]+)"', html).group(1)


def register(client, username="aigerim", password="secret123", family="Тест отбасы"):
    return client.post("/register", data={
        "csrf_token": token(client, "/register"), "username": username, "family_name": family,
        "password": password, "password2": password}, follow_redirects=True)


def post(client, url, page, **data):
    data["csrf_token"] = token(client, page)
    return client.post(url, data=data, follow_redirects=True)


def category_id(app, name, kind="expense", username="aigerim"):
    with app.app_context():
        from app import get_db
        return get_db().execute(
            "SELECT c.id FROM categories c JOIN users u ON u.id = c.user_id "
            "WHERE c.name = ? AND c.kind = ? AND u.username = ?", (name, kind, username)).fetchone()[0]


# ---- Көмекші функциялар ---------------------------------------------------
def test_helpers():
    assert parse_amount("15 000") == 15000
    assert parse_amount("0") is None
    assert parse_amount("-5") is None
    assert parse_amount("12.5") is None
    assert parse_amount("abc") is None
    assert fmt_money(1234567).replace(" ", " ") == "1 234 567 ₸"
    assert shift_month("2026-01", -1) == "2025-12"
    assert shift_month("2026-12", 1) == "2027-01"
    assert month_range("2026-12") == ("2026-12-01", "2027-01-01")


# ---- Аутентификация -------------------------------------------------------
def test_redirects_to_login_when_anonymous(client):
    for url in ("/dashboard", "/transactions", "/reports", "/budgets"):
        r = client.get(url)
        assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_register_login_logout(client):
    r = register(client)
    assert "Тіркелу сәтті өтті" in r.get_data(as_text=True)
    assert "Шолу" in r.get_data(as_text=True)
    post(client, "/logout", "/dashboard")
    assert client.get("/dashboard").status_code == 302
    r = post(client, "/login", "/login", username="AIGERIM", password="secret123")
    assert "Қалдық" in r.get_data(as_text=True)


def test_wrong_password_and_duplicate_username(client):
    register(client)
    post(client, "/logout", "/dashboard")
    r = post(client, "/login", "/login", username="aigerim", password="nope")
    assert "Логин немесе құпиясөз қате" in r.get_data(as_text=True)
    r = register(client)
    assert "бос емес" in r.get_data(as_text=True)


def test_register_validation(client):
    r = register(client, username="a b", password="123")
    html = r.get_data(as_text=True)
    assert "Логин 3–30 таңба" in html and "Құпиясөз 6–128" in html


def test_csrf_required(client):
    register(client)
    r = client.post("/categories", data={"name": "Тест", "kind": "expense"})
    assert r.status_code == 400


# ---- Операциялар ----------------------------------------------------------
def add_tx(client, app, amount="15000", kind="expense", cat="Азық-түлік", date="2026-03-10", note="нан",
           username="aigerim"):
    return post(client, "/transactions/new", "/transactions/new", kind=kind, amount=amount,
                tx_date=date, category_id=category_id(app, cat, kind, username), member_id="", note=note)


def test_transaction_crud(client, app):
    register(client)
    r = add_tx(client, app)
    html = r.get_data(as_text=True)
    assert "Операция сақталды" in html and "15&nbsp;000" in html.replace(" ", "&nbsp;") or "15 000" in html

    with app.app_context():
        from app import get_db
        tx_id = get_db().execute("SELECT id FROM transactions").fetchone()[0]

    r = post(client, f"/transactions/{tx_id}/edit", f"/transactions/{tx_id}/edit", kind="expense",
             amount="20000", tx_date="2026-03-11", category_id=category_id(app, "Азық-түлік"),
             member_id="", note="өзгертілді")
    assert "Өзгерістер сақталды" in r.get_data(as_text=True)
    assert "өзгертілді" in r.get_data(as_text=True)

    r = post(client, f"/transactions/{tx_id}/delete", "/transactions", back="/transactions")
    assert "Операция жойылды" in r.get_data(as_text=True)
    with app.app_context():
        from app import get_db
        assert get_db().execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0


@pytest.mark.parametrize("kwargs, message", [
    ({"amount": "0"}, "Сома оң бүтін сан"),
    ({"amount": "12.5"}, "Сома оң бүтін сан"),
    ({"date": "2026-13-45"}, "Күнді дұрыс"),
    ({"note": "x" * 201}, "200 таңбадан"),
])
def test_transaction_validation(client, app, kwargs, message):
    register(client)
    r = add_tx(client, app, **kwargs)
    assert message in r.get_data(as_text=True)
    with app.app_context():
        from app import get_db
        assert get_db().execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0


def test_category_kind_must_match(client, app):
    register(client)
    r = post(client, "/transactions/new", "/transactions/new", kind="income", amount="1000",
             tx_date="2026-03-10", category_id=category_id(app, "Азық-түлік", "expense"), note="")
    assert "сәйкес келмейді" in r.get_data(as_text=True)


def test_users_are_isolated(client, app):
    register(client, "user_a")
    add_tx(client, app, username="user_a")
    with app.app_context():
        from app import get_db
        tx_id = get_db().execute("SELECT id FROM transactions").fetchone()[0]
    foreign_cat = category_id(app, "Азық-түлік", username="user_a")
    post(client, "/logout", "/dashboard")

    register(client, "user_b")
    assert client.get(f"/transactions/{tx_id}/edit").status_code == 404
    post(client, f"/transactions/{tx_id}/delete", "/transactions")
    r = post(client, "/transactions/new", "/transactions/new", kind="expense", amount="500",
             tx_date="2026-03-10", category_id=foreign_cat, note="")
    assert "Санатты таңдаңыз" in r.get_data(as_text=True)
    with app.app_context():
        from app import get_db
        assert get_db().execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1


def test_delete_ignores_external_redirect(client, app):
    register(client)
    add_tx(client, app)
    with app.app_context():
        from app import get_db
        tx_id = get_db().execute("SELECT id FROM transactions").fetchone()[0]
    r = client.post(f"/transactions/{tx_id}/delete",
                    data={"csrf_token": token(client, "/transactions"), "back": "https://evil.example"})
    assert r.status_code == 302 and "evil.example" not in r.headers["Location"]


def test_search_and_filters(client, app):
    register(client)
    add_tx(client, app, note="нан мен сүт")
    add_tx(client, app, amount="9000", note="бензин", cat="Көлік")
    html = client.get("/transactions?month=2026-03&q=бензин").get_data(as_text=True)
    assert "бензин" in html and "нан мен сүт" not in html
    html = client.get("/transactions?month=2026-03&q=%25").get_data(as_text=True)  # '%' әріптік мағынада
    assert "Операция табылмады" in html


# ---- Санаттар, мүшелер, лимиттер -------------------------------------------
def test_categories(client, app):
    register(client)
    r = post(client, "/categories", "/categories", name="Үй жануарлары", kind="expense")
    assert "Санат қосылды" in r.get_data(as_text=True)
    r = post(client, "/categories", "/categories", name="Үй жануарлары", kind="expense")
    assert "бұрын қосылған" in r.get_data(as_text=True)

    add_tx(client, app)
    cid = category_id(app, "Азық-түлік")
    r = post(client, f"/categories/{cid}/delete", "/categories")
    assert "жоюға болмайды" in r.get_data(as_text=True)
    cid = category_id(app, "Үй жануарлары")
    r = post(client, f"/categories/{cid}/delete", "/categories")
    assert "Санат жойылды" in r.get_data(as_text=True)


def test_members(client):
    register(client)
    r = post(client, "/members", "/members", name="Бекзат")
    assert "Бекзат" in r.get_data(as_text=True)
    r = post(client, "/members", "/members", name="Бекзат")
    assert "бұрын қосылған" in r.get_data(as_text=True)


def test_budget_limits(client, app):
    register(client)
    add_tx(client, app, amount="45000", date="2026-03-10")
    cid = category_id(app, "Азық-түлік")
    r = post(client, "/budgets", "/budgets?month=2026-03", month="2026-03", category_id=cid, limit="40000")
    html = r.get_data(as_text=True)
    assert "Лимит сақталды" in html and "лимиттен асты" in html
    r = post(client, "/budgets", "/budgets?month=2026-03", month="2026-03", category_id=cid, limit="abc")
    assert "оң бүтін сан" in r.get_data(as_text=True)
    r = post(client, "/budgets", "/budgets?month=2026-03", month="2026-03", category_id=cid, limit="")
    assert "Лимит өшірілді" in r.get_data(as_text=True)


def test_copy_limits_from_previous_month(client, app):
    register(client)
    cid = category_id(app, "Азық-түлік")
    post(client, "/budgets", "/budgets?month=2026-02", month="2026-02", category_id=cid, limit="100000")
    r = post(client, "/budgets/copy", "/budgets?month=2026-03", month="2026-03")
    assert "1 лимит көшірілді" in r.get_data(as_text=True)
    assert "100000" in client.get("/budgets?month=2026-03").get_data(as_text=True)


# ---- Есептер ---------------------------------------------------------------
def test_dashboard_and_report_numbers(client, app):
    register(client)
    add_tx(client, app, amount="400000", kind="income", cat="Жалақы", date="2026-03-05")
    add_tx(client, app, amount="100000", date="2026-03-10")
    html = client.get("/dashboard?month=2026-03").get_data(as_text=True).replace(" ", " ")
    assert "400 000 ₸" in html and "100 000 ₸" in html and "300 000 ₸" in html
    html = client.get("/reports?month=2026-03").get_data(as_text=True).replace(" ", " ")
    assert "Жинақтау үлесі: 75%" in html


def test_csv_export(client, app):
    register(client)
    add_tx(client, app, note="=1+1")
    r = client.get("/reports/export.csv?month=2026-03")
    assert r.status_code == 200 and r.mimetype == "text/csv"
    text = r.get_data(as_text=True)
    assert text.startswith("﻿Күні;Түрі;Санат")
    assert "'=1+1" in text  # формула инъекциясынан қорғау


def test_invalid_month_falls_back(client):
    register(client)
    assert client.get("/dashboard?month=abc").status_code == 200
    assert client.get("/dashboard?month=0000-99").status_code == 200


def test_404_page(client):
    register(client)
    r = client.get("/nope")
    assert r.status_code == 404 and "Бет табылмады" in r.get_data(as_text=True)


def test_security_headers(client):
    r = client.get("/login")
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]
