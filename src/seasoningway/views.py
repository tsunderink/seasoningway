"""預訂、行程、旅程總覽與下一步的視圖。"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from .budget import (PLAN_STATUSES, budget_warnings, cell, committed_line, compute_budget,
                     render_budget, target_line, total)
from .common import GENERATED, INDEX, TRIPS, Problem
from .records import read_record
from .trips import Trip, load_trips, validate_trip


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
    out = [GENERATED.format(how="修改 bookings.json 後執行 seasoningway build"), "", f"# 預訂清單：{trip.title}", ""]
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
    out = [GENERATED.format(how="修改 itinerary.json 後執行 seasoningway build"), "", f"# 逐日行程：{trip.title}", ""]
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


def write_views(trip: Trip, root: Path, today: date) -> list[str]:
    """寫出視圖，回傳寫了哪些檔；有問題時寫完沒問題的再丟出 Problem。"""
    views, problems, _ = trip_views(trip, root, today)
    for name, text in views.items():
        (trip.path / name).write_text(text, encoding="utf-8", newline="\n")
    if problems:
        raise Problem("\n".join(problems))
    return list(views)


# ---------------------------------------------------------------- 總覽


def render_index(trips: list[Trip]) -> str:
    def table(items: list[Trip]) -> list[str]:
        if not items:
            return ["（目前沒有）"]
        rows = ["| 旅程 | 狀態 | 日期 | 人數 |", "|---|---|---|--:|"]
        for t in items:
            rows.append(f"| [{cell(t.title)}](trips/{t.slug}/README.md) | {cell(t.status)} | {t.dates()} | {cell(t.meta.get('travelers', ''))} |")
        return rows

    active = [t for t in trips if t.active]
    done = [t for t in reversed(trips) if not t.active]
    out = [
        GENERATED.format(how="執行 seasoningway build 更新"),
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
        "- [旅行偏好](profile/preferences.md)",
        "- [跨旅程心得](profile/lessons.md)",
        "- [想去的地方](wishlist.md)",
    ]
    return "\n".join(out) + "\n"


def write_index(root: Path) -> None:
    (root / INDEX).write_text(render_index(load_trips(root)), encoding="utf-8", newline="\n")


def section_lines(path: Path, heading: str) -> list[str]:
    lines, inside = [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            inside = line[3:].strip() == heading
        elif inside and line.strip() and not line.strip().startswith("<!--"):
            lines.append(line.rstrip())
    return lines


def print_active_trips(root: Path) -> int:
    active = [t for t in load_trips(root) if t.active]
    if not active:
        print("目前沒有進行中的旅程。")
    for t in active:
        print(f"- {t.slug}｜{t.title}｜{t.status}｜{t.dates()}")
        try:
            if t.budget_target is not None and (t.path / "budget.json").exists():
                b = compute_budget(t, root)
                print("    " + target_line(t, total(b.lines, None, PLAN_STATUSES)) + "；" + committed_line(b))
        except Problem as e:
            print(f"    預算算不出來：{e}")
        steps = section_lines(t.path / "README.md", "下一步")
        for line in steps[:LIST_STEPS]:
            print(f"    下一步 {brief(line)}")
        if len(steps) > LIST_STEPS:
            print(f"    ……還有 {len(steps) - LIST_STEPS} 項")
        if steps:
            print(f"    （每項只列開頭，全文在 {TRIPS}/{t.slug}/README.md）")
    return 0


LIST_STEPS = 8
BRIEF_CHARS = 70


def brief(line: str, limit: int = BRIEF_CHARS) -> str:
    """SessionStart hook 每次開 session、壓縮對話都會印 list，所以每項只留開頭，省 context。"""
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line.strip().removeprefix("- "))
    return text if len(text) <= limit else text[: limit - 1] + "…"
