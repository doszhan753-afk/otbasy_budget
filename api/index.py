"""Vercel үшін кіру нүктесі.

Vercel-де жоба файлдары тек оқуға арналған, сондықтан дерекқор /tmp ішінде
жасалады (уақытша: функция қайта іске қосылғанда деректер өшеді).
SECRET_KEY Vercel жобасының ортасында (Environment Variables) берілуі керек.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_PATH", "/tmp/budget.db")

from app import create_app  # noqa: E402

app = create_app()
