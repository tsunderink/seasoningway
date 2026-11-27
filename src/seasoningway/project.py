"""以套件範本建立旅程與升級資料格式。"""
from __future__ import annotations

import argparse
import re
from datetime import date
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

from . import config
from .common import CONFIG, CURRENCY_RE, SLUG_RE, TRIPS, Problem
from .files import write_text
from .trips import Trip, load_trip
from .migration import upgrade_v4

TEMPLATES = files("seasoningway").joinpath("templates")


def template_files(folder: Traversable, prefix: Path = Path()):
    """列出資源檔及相對路徑，與套件安裝位置無關。"""
    for entry in sorted(folder.iterdir(), key=lambda item: item.name):
        relative = prefix / entry.name
        if entry.is_dir():
            yield from template_files(entry, relative)
        else:
            yield relative, entry


def create_trip(root: Path, slug: str, title: str | None, travelers: int, currency: str | None, today: date) -> Trip:
    if not SLUG_RE.fullmatch(slug):
        raise Problem("旅程代號要是 YYYY-MM-英文地名，例如 2099-01-example-city（小寫英數字，用 - 連接）")
    dest = root / TRIPS / slug
    if dest.exists():
        raise Problem(f"{TRIPS}/{slug} 已經存在")
    currency = (currency or config.load_config(root).default_currency).upper()
    if not CURRENCY_RE.fullmatch(currency):
        raise Problem(f"請用 --currency 指定基準幣別（例如 TWD），或在 {CONFIG} 設定 budget.default_currency")
    if travelers < 1:
        raise Problem("--travelers 必須是正整數")
    values = {
        "title": " ".join((title or slug).split()),
        "currency": currency,
        "travelers": str(travelers),
        "created": today.isoformat(),
    }
    dest.mkdir(parents=True)
    for relative, resource in template_files(TEMPLATES / "trip"):
        path = dest / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        text = resource.read_text(encoding="utf-8")
        text = re.sub(r"\{\{(\w+)\}\}", lambda match: values[match[1]], text)
        path.write_text(text, encoding="utf-8", newline="\n")
    (dest / "research").mkdir(exist_ok=True)
    return load_trip(root, slug)


OLDEST = 3  # 能升級的最舊版本；更舊的轉換程式在資料都轉完後刪了（在 git 歷史裡）
MIGRATIONS = {4: upgrade_v4}  # 從 OLDEST 往上，升到第 n 版要做的事：{n: 函式(root) -> 做了什麼}；每一步只能依賴上一版的格式


def set_format(root: Path, version: int) -> None:
    path = root / CONFIG
    text = path.read_text(encoding="utf-8")
    new, n = re.subn(r"(?m)^format\s*=\s*\d+", f"format = {version}", text, count=1)
    write_text(path, new if n else f"format = {version}\n" + text)


def cmd_migrate(args: argparse.Namespace, root: Path, today: date) -> int:
    assert sorted(MIGRATIONS) == list(range(OLDEST + 1, config.FORMAT + 1)), "MIGRATIONS 要涵蓋 OLDEST+1..FORMAT"
    if not (root / CONFIG).is_file():
        raise Problem(f"找不到 {CONFIG}：migrate 需要已建立的資料 repo")
    start = config.load_config(root).format
    if start == 0:
        raise Problem(f"{CONFIG} 還沒初始化：migrate 需要第 {OLDEST} 版以上的資料")
    if start > config.FORMAT:
        raise Problem(f"{CONFIG} 是第 {start} 版，比這版引擎（第 {config.FORMAT} 版）新：更新引擎")
    if start < OLDEST:
        raise Problem(f"{CONFIG} 是第 {start} 版，這版引擎只能從第 {OLDEST} 版升級：先用舊版的引擎 migrate")
    for version in range(start + 1, config.FORMAT + 1):
        for line in MIGRATIONS[version](root):
            print(f"第 {version} 版：{line}")
        set_format(root, version)
    how = "（本來就是）" if start == config.FORMAT else f"（從第 {start} 版升級）"
    print(f"資料格式是第 {config.FORMAT} 版{how}")
    return 0


def cmd_new(args: argparse.Namespace, root: Path, today: date) -> int:
    trip = create_trip(root, args.slug, args.title, args.travelers, args.currency, today)
    print(f"已建立 {TRIPS}/{trip.slug}/（{trip.title}，{trip.travelers} 人，基準幣別 {trip.currency}）")
    return 0
