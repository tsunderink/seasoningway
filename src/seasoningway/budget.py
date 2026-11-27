"""預算、匯率換算與預算表。"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from .common import GENERATED, TRIPS, Problem
from .records import STALE_DAYS, read_record, stale_values
from .trips import Trip, load_trip, validate_trip


BUDGET_STATUSES = ("預估", "已訂", "實付")
PLAN_STATUSES = ("預估", "已訂")
ZERO_DECIMAL = frozenset({"JPY", "KRW", "TWD", "VND", "IDR", "CLP", "ISK", "HUF"})


@dataclass
class Rate:
    currency: str
    rate: Decimal
    as_of: date
    source: dict


@dataclass
class Line:
    where: str
    category: str
    item: str
    unit_amount: Decimal
    currency: str
    qty: Decimal
    per_person: bool
    status: str
    as_of: date
    source: dict | None
    note: str
    base: Decimal  # 換算成基準幣別後的金額


@dataclass
class Budget:
    trip: Trip
    travelers: int
    lines: list[Line]
    rates: dict[str, Rate]


def compute_budget(trip: Trip, root: Path) -> Budget:
    """讀 budget.json（格式在 schemas/budget.schema.json），換算成基準幣別。"""
    errors = validate_trip(trip)
    if errors:
        raise Problem("\n".join(errors))
    rel = f"{TRIPS}/{trip.slug}/budget.json"
    data = read_record(root, trip.path / "budget.json")
    travelers = trip.travelers
    rates = {r["currency"]: Rate(r["currency"], Decimal(r["rate"]), date.fromisoformat(r["as_of"]), r["source"])
             for r in data["rates"]}
    errors = [f"{rel}：rates：{trip.currency} 是基準幣別，不用列匯率"] if trip.currency in rates else []
    errors += [f"{TRIPS}/{trip.slug}/README.md：show_also 列了 {cur}，但 budget.json 的 rates 沒有 {cur} 的匯率"
               for cur in trip.show_also if cur not in rates]
    lines = []
    for i, it in enumerate(data["items"]):
        where, cur = f"{rel} items[{i}]", it["currency"]
        if cur != trip.currency and cur not in rates:
            errors.append(f"{where}（{it['item'][:20]}）：rates 沒有 {cur} 的匯率")
            continue
        unit, qty = Decimal(it["unit_amount"]), Decimal(it.get("qty", 1))
        per_person = it.get("scope") == "每人"
        base = unit * qty * (travelers if per_person else 1) * (rates[cur].rate if cur in rates else Decimal(1))
        lines.append(Line(where, it["category"], it["item"], unit, cur, qty, per_person, it["status"],
                          date.fromisoformat(it["as_of"]), it.get("source"), it.get("note", ""), base))
    if errors:
        raise Problem("\n".join(errors))
    return Budget(trip, travelers, lines, rates)


def convert(b: Budget, amount: Decimal, currency: str) -> Decimal:
    """把基準幣別金額換算成 rates 裡的另一個幣別。"""
    return amount / b.rates[currency].rate


def money(amount: Decimal, currency: str) -> str:
    step = Decimal(1) if currency in ZERO_DECIMAL else Decimal("0.01")
    value = amount.quantize(step, rounding=ROUND_HALF_UP)
    if value == 0:
        value = abs(value)
    return f"{value:,}"


def signed(amount: Decimal, currency: str) -> str:
    text = money(amount, currency)
    return text if text.startswith("-") or amount.quantize(Decimal("0.01")) == 0 else f"+{text}"


def number(value: Decimal) -> str:
    return f"{value:,}"


def cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def source_cell(source: dict | None) -> str:
    """schemas/common.schema.json 的 source：{url, title, via}。"""
    if not source:
        return ""
    text = f"[{cell(source.get('title', '連結'))}]({source['url']})"
    return text + (f"（{source['via']}）" if source.get("via") else "")


def categories(lines: list[Line]) -> list[str]:
    return list(dict.fromkeys(line.category for line in lines))


def total(lines: list[Line], category: str | None = None, statuses: tuple[str, ...] = BUDGET_STATUSES) -> Decimal:
    return sum(
        (l.base for l in lines if l.status in statuses and (category is None or l.category == category)),
        Decimal(0),
    )


def render_budget(b: Budget) -> str:
    t, cur, n = b.trip, b.trip.currency, b.travelers
    also = t.show_also

    def extra(amount: Decimal, bold: bool = False) -> str:
        mark = "**" if bold else ""
        return "".join(f" {mark}{money(convert(b, amount, c), c)}{mark} |" for c in also)

    def extra_head(*labels: str) -> tuple[str, str]:
        heads = [f" {label}（{c}） |" for label in labels for c in also]
        return "".join(heads), "--:|" * len(heads)

    plan = [l for l in b.lines if l.status in PLAN_STATUSES]
    actual = [l for l in b.lines if l.status == "實付"]
    out = [
        GENERATED.format(how=f"修改 budget.json 後執行 seasoningway budget {t.slug}"),
        "",
        f"# 預算摘要：{t.title}",
        "",
        f"基準幣別 {cur}｜人數 {n}｜表中金額都已換算成 {cur}"
        + (f"，並另列 {'、'.join(also)}（依 budget.json 的匯率換算）" if also else ""),
        *(["", target_line(t, total(plan, None, PLAN_STATUSES)) + "；" + committed_line(b)] if t.budget_target is not None else []),
        "",
        "## 計畫（預估＋已訂）",
        "",
    ]
    if plan:
        head, rule = extra_head("小計", "每人")
        out += [f"| 類別 | 預估 | 已訂 | 小計 | 每人 |{head}", f"|---|--:|--:|--:|--:|{rule}"]
        for c in categories(plan):
            est, booked = total(plan, c, ("預估",)), total(plan, c, ("已訂",))
            out.append(
                f"| {cell(c)} | {money(est, cur)} | {money(booked, cur)} | {money(est + booked, cur)} | {money((est + booked) / n, cur)} |"
                + extra(est + booked) + extra((est + booked) / n)
            )
        est, booked = total(plan, None, ("預估",)), total(plan, None, ("已訂",))
        out.append(
            f"| **合計** | **{money(est, cur)}** | **{money(booked, cur)}** | **{money(est + booked, cur)}** | **{money((est + booked) / n, cur)}** |"
            + extra(est + booked, True) + extra((est + booked) / n, True)
        )
    else:
        out.append("（尚無資料）")

    out += ["", "## 實際花費（旅後概算）", ""]
    if actual:
        head, rule = extra_head("實際")
        out += [f"| 類別 | 計畫 | 實際 | 差距 |{head}", f"|---|--:|--:|--:|{rule}"]
        for c in categories(plan + actual):
            p, a = total(plan, c), total(actual, c)
            out.append(f"| {cell(c)} | {money(p, cur)} | {money(a, cur)} | {signed(a - p, cur)} |" + extra(a))
        p, a = total(plan), total(actual)
        out.append(f"| **合計** | **{money(p, cur)}** | **{money(a, cur)}** | **{signed(a - p, cur)}** |" + extra(a, True))
    else:
        out.append("（尚未記錄）")

    out += ["", "## 明細", ""]
    if b.lines:
        head, rule = extra_head("金額")
        out += [
            f"| 類別 | 項目 | 計算 | 狀態 | 金額（{cur}） |{head} 查詢日期 | 來源 | 備註 |",
            f"|---|---|---|---|--:|{rule}---|---|---|",
        ]
        for l in b.lines:
            calc = f"{number(l.unit_amount)} {l.currency}"
            if l.qty != 1:
                calc += f" × {number(l.qty)}"
            if l.per_person:
                calc += f" × {n} 人"
            out.append(
                f"| {cell(l.category)} | {cell(l.item)} | {calc} | {l.status} | {money(l.base, cur)} |"
                + extra(l.base)
                + f" {l.as_of.isoformat()} | {source_cell(l.source)} | {cell(l.note)} |"
            )
    else:
        out.append("（尚無資料）")

    out += ["", "## 匯率", ""]
    if b.rates:
        out += [f"| 幣別 | 1 單位 = {cur} | 查詢日期 | 來源 |", "|---|--:|---|---|"]
        for r in b.rates.values():
            out.append(f"| {r.currency} | {number(r.rate)} | {r.as_of.isoformat()} | {source_cell(r.source)} |")
    else:
        out.append("（只使用基準幣別）")
    return "\n".join(out) + "\n"


def budget_warnings(b: Budget, today: date) -> list[str]:
    if not b.trip.active:
        return []
    warnings = [
        f"{l.where}：「{l.item}」的估價查詢於 {l.as_of}，已超過 {STALE_DAYS} 天，用來做決定前要重查"
        for l in b.lines
        if l.status == "預估" and (today - l.as_of).days > STALE_DAYS
    ]
    warnings += [
        f"{TRIPS}/{b.trip.slug}/budget.json rates：{r.currency} 匯率查詢於 {r.as_of}，已超過 {STALE_DAYS} 天，用來做決定前要重查"
        for r in b.rates.values()
        if (today - r.as_of).days > STALE_DAYS
    ]
    return warnings


def target_line(trip: Trip, plan: Decimal) -> str:
    target, cur = trip.budget_target, trip.currency
    gap = plan - target
    return (f"預算目標 {money(target, cur)} {cur}（README 開頭的 budget_target），計畫"
            f"{'超出' if gap > 0 else '還剩'} {money(abs(gap), cur)} {cur}，是目標的 {plan / target:.0%}")


PENDING_AMOUNT = "金額待補"  # 已經訂了、實付金額還沒補的預估列，項目名稱寫「（已訂，金額待補）」


def committed_line(b: Budget) -> str:
    """已訂加上「已訂、金額待補」的估價：取消不了或不打算取消的部分，佔預算目標多少。"""
    pending = [l for l in b.lines if l.status == "預估" and PENDING_AMOUNT in l.item]
    amount = total(b.lines, None, ("已訂",)) + sum((l.base for l in pending), Decimal(0))
    cur, target = b.trip.currency, b.trip.budget_target
    return (f"已訂合計 {money(amount, cur)} {cur}（含 {len(pending)} 筆{PENDING_AMOUNT}的估價），"
            f"是目標的 {amount / target:.0%}")


def budget_summary(b: Budget) -> list[str]:
    cur, n = b.trip.currency, b.travelers
    plan = total(b.lines, None, PLAN_STATUSES)
    lines = [
        f"計畫合計：{money(plan, cur)} {cur}（每人 {money(plan / n, cur)} {cur}）",
        f"  其中預估 {money(total(b.lines, None, ('預估',)), cur)}、已訂 {money(total(b.lines, None, ('已訂',)), cur)}",
    ]
    for c in b.trip.show_also:
        p = convert(b, plan, c)
        lines.append(f"  約 {money(p, c)} {c}（每人 {money(p / n, c)} {c}）")
    if b.trip.budget_target is not None:
        lines.append("  " + target_line(b.trip, plan))
        lines.append("  " + committed_line(b))
    if any(l.status == "實付" for l in b.lines):
        actual = total(b.lines, None, ("實付",))
        lines.append(f"實際合計：{money(actual, cur)} {cur}（與計畫差距 {signed(actual - plan, cur)}）")
        for c in b.trip.show_also:
            lines.append(f"  約 {money(convert(b, actual, c), c)} {c}")
    return lines


def write_budget(trip: Trip, root: Path) -> Budget:
    b = compute_budget(trip, root)
    (trip.path / "budget.md").write_text(render_budget(b), encoding="utf-8", newline="\n")
    return b


def cmd_budget(args: argparse.Namespace, root: Path, today: date) -> int:
    b = write_budget(load_trip(root, args.slug), root)
    print(f"已更新 {TRIPS}/{args.slug}/budget.md")
    print("\n".join(budget_summary(b)))
    for w in budget_warnings(b, today):
        print(f"⚠️ {w}")
    return 0
