"""旅程 README 的欄位與驗證。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .common import DONE, SLUG_RE, TRIPS, Problem, real_date
from .records import parse_front_matter, record_problems


# ---------------------------------------------------------------- 旅程


@dataclass
class Trip:
    slug: str
    path: Path
    meta: dict[str, str]

    @property
    def title(self) -> str:
        return self.meta.get("title") or self.slug

    @property
    def status(self) -> str:
        return self.meta.get("status", "")

    @property
    def currency(self) -> str:
        return self.meta.get("currency", "")

    @property
    def show_also(self) -> list[str]:
        """budget.md 另外並列的幣別（README 開頭的 show_also，可用逗號分隔多個）。"""
        raw = self.meta.get("show_also", "")
        return [c.strip().upper() for c in re.split(r"[,，、\s]+", raw) if c.strip()]

    @property
    def budget_target(self) -> Decimal | None:
        """README 開頭的 budget_target：全體合計的預算目標，基準幣別；空白＝沒定。"""
        raw = self.meta.get("budget_target", "").replace(",", "")
        if not raw:
            return None
        try:
            value = Decimal(raw)
        except InvalidOperation:
            value = Decimal(-1)
        if value <= 0:
            raise Problem(f"{TRIPS}/{self.slug}/README.md：budget_target 要是正數（基準幣別、全體合計），目前是「{raw}」")
        return value

    @property
    def active(self) -> bool:
        return self.status != DONE

    @property
    def travelers(self) -> int:
        raw = self.meta.get("travelers", "")
        if not raw.isdigit() or int(raw) < 1:
            raise Problem(f"{TRIPS}/{self.slug}/README.md：travelers 必須是正整數，目前是「{raw}」")
        return int(raw)

    def dates(self) -> str:
        start, end = self.meta.get("start", ""), self.meta.get("end", "")
        if start and end:
            return f"{start} ～ {end}"
        return start or end or "未定"


def read_trip(path: Path) -> Trip:
    return Trip(path.name, path, parse_front_matter((path / "README.md").read_text(encoding="utf-8")))


def load_trips(root: Path) -> list[Trip]:
    trips_dir = root / TRIPS
    if not trips_dir.is_dir():
        return []
    return [read_trip(d) for d in sorted(trips_dir.iterdir()) if (d / "README.md").is_file()]


def load_trip(root: Path, slug: str) -> Trip:
    if not SLUG_RE.fullmatch(slug):
        raise Problem("旅程代號要是 YYYY-MM-英文地名，例如 2099-01-example-city")
    path = root / TRIPS / slug
    if not (path / "README.md").is_file():
        raise Problem(f"找不到 {TRIPS}/{slug}/README.md")
    return read_trip(path)


def validate_trip(trip: Trip) -> list[str]:
    """README 開頭的設定：欄位由 schemas/trip.schema.json 定義，這裡加上 schema 管不到的。"""
    where = f"{TRIPS}/{trip.slug}/README.md"
    errors = []
    if not SLUG_RE.match(trip.slug):
        errors.append(f"{TRIPS}/{trip.slug}：資料夾名稱要是 YYYY-MM-英文地名（小寫英數字，用 - 連接）")
    if not trip.meta:
        return errors + [f"{where}：開頭缺少 --- 包住的設定區塊"]
    errors += record_problems(f"{TRIPS}/{trip.slug}/README.md", (trip.path / "README.md").read_text(encoding="utf-8"))
    if errors:
        return errors
    errors += [f"{where}：show_also 不用列基準幣別 {cur}" for cur in trip.show_also if cur == trip.currency]
    start, end = (real_date(trip.meta.get(k, "")) for k in ("start", "end"))
    if start and end and end < start:
        errors.append(f"{where}：end（{end}）早於 start（{start}）")
    try:
        trip.budget_target
    except Problem as e:
        errors.append(str(e))
    return errors


def section_lines(path: Path, heading: str) -> list[str]:
    lines, inside = [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            inside = line[3:].strip() == heading
        elif inside and line.strip() and not line.strip().startswith("<!--"):
            lines.append(line.rstrip())
    return lines
