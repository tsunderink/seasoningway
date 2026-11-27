"""預訂、行程與旅程總覽的視圖。"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from .budget import budget_warnings, cell, compute_budget, render_budget
from .common import GENERATED, PROFILE, TRIPS, WISHLIST, Problem
from .records import read_record
from .trips import Trip


OPEN_BOOKINGS = ("待訂", "看需要")
WEEKDAYS = "一二三四五六日"


def row(*cells: str) -> str:
    return "| " + " | ".join(cell(c) for c in cells) + " |"


def render_bookings(trip: Trip, data: dict) -> str:
    items = data["items"]

    def status(b: dict) -> str:
        return b["status"] + (f"（{b['status_note']}）" if b.get("status_note") else "")

    open_ = sorted((b for b in items if b["status"] in OPEN_BOOKINGS), key=lambda b: b.get("book_on", ""))
    booked = sorted((b for b in items if b["status"] == "已訂"), key=lambda b: b.get("date", "9999"))
    skipped = [b for b in items if b["status"] == "不需要"]
    out = [GENERATED.format(how="修改 bookings.json 後執行 seasoningway render"), "", f"# 預訂清單：{trip.title}", ""]
    if data.get("note"):
        out += [data["note"], ""]
    out += ["## 待訂（依預訂時間排）", ""]
    out += (["| 項目 | 建議預訂時間 | 狀態 | 平台 | 備註 |", "|---|---|---|---|---|"]
            + [row(b["item"], b.get("when", "—"), status(b), b.get("platform", "—"), b.get("note", "")) for b in open_]
            if open_ else ["（沒有）"])
    out += ["", "## 已訂（依日期排）", ""]
    out += (["| 項目 | 狀態 | 平台 | 備註 |", "|---|---|---|---|"]
            + [row(b["item"], status(b), b.get("platform", "（待補）"), b.get("note", "")) for b in booked]
            if booked else ["（沒有）"])
    if skipped:
        out += ["", "## 不需要", "", "| 項目 | 備註 |", "|---|---|"]
        out += [row(b["item"], b.get("status_note", b.get("note", ""))) for b in skipped]
    return "\n".join(out) + "\n"


def booking_warnings(trip: Trip, data: dict, today: date) -> list[str]:
    if not trip.active:
        return []
    return [f"{TRIPS}/{trip.slug}/bookings.json：「{b['item'][:30]}」打算 {b['book_on']} 訂，還是{b['status']}；訂了改成已訂，延後就改 book_on"
            for b in data["items"] if b["status"] == "待訂" and b.get("book_on", "9999") < today.isoformat()]


def render_itinerary(trip: Trip, data: dict) -> str:
    out = [GENERATED.format(how="修改 itinerary.json 後執行 seasoningway render"), "", f"# 逐日行程：{trip.title}", ""]
    if data.get("summary"):
        out += [data["summary"], ""]
    if data["days"]:
        out += ["| 日期 | 去哪 | 住 | 交通 |", "|---|---|---|---|"]
        previous = None
        for d in data["days"]:
            day = date.fromisoformat(d["date"])
            label = f"{day.month}/{day.day}（{WEEKDAYS[day.weekday()]}" + (f"，{d['day_note']}" if d.get("day_note") else "") + "）"
            stay = d.get("stay")
            out.append(row(label, d.get("plan", ""), "同上" if stay and stay == previous else stay or "—", d.get("travel", "—")))
            previous = stay
    else:
        out.append("（還沒排）")
    if data.get("notes"):
        out += ["", data["notes"]]
    return "\n".join(out) + "\n"


VIEW_SOURCES = {"bookings.md": ("bookings.json", render_bookings), "itinerary.md": ("itinerary.json", render_itinerary)}


def trip_views(trip: Trip, root: Path, today: date) -> tuple[dict[str, str], list[str], list[str]]:
    """一趟旅程的視圖：回傳（{檔名: 內容}, 問題, 提醒）。一個來源有問題，其他照樣產生。"""
    views, problems, warnings = {}, [], []
    try:
        b = compute_budget(trip, root)
        views["budget.md"] = render_budget(b)
        warnings += budget_warnings(b, today)
    except Problem as e:
        problems += str(e).splitlines()
    for name, (source, render) in VIEW_SOURCES.items():
        try:
            data = read_record(root, trip.path / source)
        except Problem as e:
            problems += str(e).splitlines()
            continue
        views[name] = render(trip, data)
        if source == "bookings.json":
            warnings += booking_warnings(trip, data, today)
    return views, problems, warnings


def render_index(trips: list[Trip]) -> str:
    def table(items: list[Trip]) -> list[str]:
        if not items:
            return ["（目前沒有）"]
        rows = ["| 旅程 | 狀態 | 日期 | 人數 |", "|---|---|---|--:|"]
        for t in items:
            rows.append(f"| [{cell(t.title)}](../trips/{t.slug}/README.md) | {cell(t.status)} | {t.dates()} | {cell(t.meta.get('travelers', ''))} |")
        return rows

    active = [t for t in trips if t.active]
    done = [t for t in reversed(trips) if not t.active]
    out = [
        GENERATED.format(how="執行 seasoningway render 更新"),
        "",
        "# 旅程規劃",
        "",
        "所有旅程的規劃都在這裡，由 Claude Code 或 Codex 依 AGENTS.md 的規則維護。",
        "",
        "## 進行中",
        "",
        *table(active),
        "",
        "## 已結束",
        "",
        *table(done),
        "",
        "## 其他",
        "",
        f"- [旅行偏好](../{PROFILE}/preferences.md)",
        f"- [跨旅程心得](../{PROFILE}/lessons.md)",
        f"- [想去的地方](../{WISHLIST})",
    ]
    return "\n".join(out) + "\n"
