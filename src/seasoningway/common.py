"""資料目錄名稱、共用格式與錯誤。"""
from __future__ import annotations

import re
from datetime import date


CONFIG = "seasoningway.toml"
TRIPS = "trips"
PROFILE = "profile"
WISHLIST = "wishlist.md"
OUT = "out"
DATA_FILES = (CONFIG, WISHLIST)  # 資料 repo 根目錄裡由使用者和 agent 維護的檔案
DATA_DIRS = (TRIPS, PROFILE)  # 底下的新檔 save 會直接加入


def data_path(rel: str) -> bool:
    """資料路徑：save 會加入的新檔都要在這裡。rel 是相對於資料 repo 根目錄的 POSIX 路徑。"""
    return rel in DATA_FILES or ("/" in rel and rel.split("/", 1)[0] in DATA_DIRS)

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

# 引擎 repo 公開的文件。資料裡連過去的是引擎說明，不是研究來源。
ENGINE_DOCS = "https://github.com/tsunderink/seasoningway/blob/main/docs/"
