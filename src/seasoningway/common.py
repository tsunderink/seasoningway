"""資料目錄名稱、共用格式與錯誤。"""
from __future__ import annotations

import re
from datetime import date


CONFIG = "seasoningway.toml"
TRIPS = "trips"
OUT = "out"

DONE = "已結束"

SLUG_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])-[a-z0-9]+(?:-[a-z0-9]+)*$")
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

GENERATED = "<!-- 由 seasoningway 產生，請勿手動編輯。{how} -->"


class Problem(Exception):
    """使用者可以修正的資料問題。"""


def real_date(text: str) -> date | None:
    """YYYY-MM-DD 是不存在的日期（例如 2-30）時回傳 None。"""
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None
