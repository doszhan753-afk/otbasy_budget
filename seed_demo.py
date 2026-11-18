"""Демо-деректер жасайды: логин demo / құпиясөз demo1234.

Қолдану:  python seed_demo.py
Бұрынғы demo аккаунты болса, ол өшіріліп, қайта жасалады.
"""
import random
from datetime import date

from werkzeug.security import generate_password_hash

from app import (MONTHS_KK, create_app, get_db, month_range, seed_user_defaults,
                 shift_month, current_month)

random.seed(2026)

MEMBERS = ["Әке", "Ана", "Айгерім", "Бекзат"]

# (санат, мүше, соманың шегі, айына неше рет, түсініктеме)
EXPENSES = [
    ("Азық-түлік", None, (4_000, 18_000), 9, "Дүкен, базар"),
    ("Пәтер ақысы", "Әке", (150_000, 150_000), 1, "Пәтер жалдау ақысы"),
    ("Коммуналдық төлемдер", "Ана", (28_000, 42_000), 1, "Жарық, су, жылу"),
    ("Көлік", "Әке", (5_000, 25_000), 3, "Жанар-жағармай"),
    ("Байланыс және интернет", "Ана", (6_000, 9_000), 1, "Интернет пен ұялы байланыс"),
    ("Денсаулық", None, (3_000, 20_000), 1, "Дәріхана"),
    ("Білім", "Айгерім", (15_000, 40_000), 1, "Үйірме ақысы"),
    ("Киім", "Ана", (8_000, 45_000), 1, "Киім-кешек"),
    ("Демалыс және ойын-сауық", None, (5_000, 30_000), 2, "Кино, кафе"),
]
INCOMES = [
    ("Жалақы", "Әке", 420_000, 1, "Айлық жалақы"),
    ("Жалақы", "Ана", 310_000, 1, "Айлық жалақы"),
]


def main():
    app = create_app()
    with app.app_context():
        db = get_db()
        db.execute("DELETE FROM users WHERE username = 'demo'")
        cur = db.execute(
            "INSERT INTO users(username, password_hash, family_name) VALUES (?, ?, ?)",
            ("demo", generate_password_hash("demo1234"), "Демо отбасы"))
        uid = cur.lastrowid
        seed_user_defaults(db, uid)
        for name in MEMBERS:
            db.execute("INSERT INTO members(user_id, name) VALUES (?, ?)", (uid, name))

        cats = {(r["name"], r["kind"]): r["id"] for r in
                db.execute("SELECT id, name, kind FROM categories WHERE user_id = ?", (uid,))}
        members = {r["name"]: r["id"] for r in
                   db.execute("SELECT id, name FROM members WHERE user_id = ?", (uid,))}

        this_month = current_month()
        today = date.today()
        rows = []
        for back in range(5, -1, -1):
            month = shift_month(this_month, -back)
            y, m = int(month[:4]), int(month[5:])
            last_day = 28 if back else max(today.day, 1)  # ағымдағы айда бүгінге дейін
            for cat, member, amount, times, note in INCOMES:
                rows.append((uid, cats[(cat, "income")], members.get(member), "income",
                             amount, date(y, m, min(5, last_day)).isoformat(), note))
            for cat, member, (lo, hi), times, note in EXPENSES:
                for _ in range(times):
                    day = random.randint(1, last_day)
                    amount = round(random.randint(lo, hi), -2)
                    rows.append((uid, cats[(cat, "expense")], members.get(member), "expense",
                                 max(amount, 500), date(y, m, day).isoformat(), note))
        db.executemany(
            "INSERT INTO transactions(user_id, category_id, member_id, kind, amount, tx_date, note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)", rows)

        # Ағымдағы айға лимиттер
        for cat, limit in [("Азық-түлік", 140_000), ("Көлік", 40_000),
                           ("Демалыс және ойын-сауық", 30_000), ("Киім", 30_000)]:
            db.execute("INSERT INTO budgets(user_id, category_id, month, limit_amount) VALUES (?, ?, ?, ?)",
                       (uid, cats[(cat, "expense")], this_month, limit))
        db.commit()
        print(f"Демо-деректер дайын: {len(rows)} операция, {MONTHS_KK[int(this_month[5:]) - 1]} айына лимиттер.")
        print("Кіру: логин  demo  /  құпиясөз  demo1234")


if __name__ == "__main__":
    main()
