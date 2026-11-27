"""升級既有的旅程資料格式。"""
import json
import posixpath
import re
from pathlib import Path
from urllib.parse import unquote

from .common import Problem, TRIPS, real_date
from .files import write_text
from .published import publication_paths
from .records import dump_json, parse_json, record_kind, record_paths, schema_url

CACHE_FILES = ("roads.txt", "roads.json", "place_ids.json", "base")
VIEWS = ("budget.md", "bookings.md", "itinerary.md")
GENERATED_START = "<!-- 由 seasoningway 產生"
LEGACY_GENERATED_START = "<!-- 由 core/trips.py 產生，請勿手動編輯。"
OLD_ATTRIBUTES = ("trips/*/budget.md", "trips/*/bookings.md", "trips/*/itinerary.md",
                  "trips/*/map/index.html", "trips/*/**/published.json", "/trip-index.md")


def ensure_out_ignored(root: Path) -> bool:
    path = root / ".gitignore"
    original = path.read_text(encoding="utf-8") if path.is_file() else ""
    if "/out/" in original.splitlines():
        return False
    prefix = original.rstrip("\n") + "\n" if original else ""
    write_text(path, prefix + "/out/\n")
    return True


def add_schema(path: Path, rel: str) -> str | None:
    text = path.read_text(encoding="utf-8")
    data = parse_json(text, rel)
    if not isinstance(data, dict):
        raise Problem(f"{rel}：紀錄要是 JSON 物件")
    url = schema_url(record_kind(rel))
    if "$schema" in data:
        if data["$schema"] != url:
            raise Problem(f"{rel}：$schema 應指向 {url}")
        return None
    # 保留原始數字、欄位順序和其他內容。
    marker = '\n  "$schema": ' + json.dumps(url) + ("," if data else "")
    return re.sub(r"\{", lambda _: "{" + marker + ("\n" if "\n" not in text[1:] else ""), text, count=1)


def generated_markdown(text: str) -> bool:
    return text.startswith((GENERATED_START, LEGACY_GENERATED_START))


def update_view_links(text: str, parent: str, converted: dict[str, str]) -> str:
    def replace(match: re.Match) -> str:
        label, target, fragment = match.groups()
        path = posixpath.normpath(posixpath.join(parent, unquote(target)))
        if path not in converted:
            return match[0]
        name = posixpath.basename(target)
        source = posixpath.basename(converted[path])
        return f"[{source if label == name else label}]({target.removesuffix(name)}{source}{fragment or ''})"

    return re.sub(r"(?<!!)\[([^]\n]+)\]\(([^()#\s]+\.(?:md|html))(#[^()\s]*)?\)", replace, text)


def upgrade_v4(root: Path) -> list[str]:
    trips = sorted(path for path in (root / TRIPS).glob("*") if path.is_dir())
    updates, moves = {}, []
    delete = [root / "trip-index.md"]
    for trip in trips:
        if not trip.resolve().is_relative_to(root.resolve()):
            raise Problem(f"{trip.relative_to(root).as_posix()}：旅程目錄指向資料 repo 外")
        for path in record_paths(trip):
            rel = path.relative_to(root).as_posix()
            if (text := add_schema(path, rel)) is not None:
                updates[path] = text
        for path in publication_paths(trip):
            rel = path.relative_to(root).as_posix()
            state = parse_json(path.read_text(encoding="utf-8"), rel)
            if not isinstance(state, dict) or not all(isinstance(state.get(k), str) and state[k] for k in ("slug", "version")):
                raise Problem(f"{rel}：發布紀錄缺少有效的 slug 或 version")
            if "inputs" not in state:
                updates[path] = dump_json({"$schema": schema_url("published"), "slug": state["slug"],
                                           "version": state["version"], "inputs": {}})
        cache = trip / "cache"
        if not cache.resolve().is_relative_to(trip.resolve()):
            raise Problem(f"{cache.relative_to(root).as_posix()}：快取目錄指向旅程外")
        for name in CACHE_FILES:
            source, target = trip / "map" / name, trip / "cache" / name
            if source.exists():
                if target.exists() or target.is_symlink():
                    raise Problem(f"{target.relative_to(root).as_posix()} 已存在：整合兩份快取後再執行 migrate")
                moves.append((source, target))
        converted_views = {}
        for name in VIEWS:
            view = trip / name
            if view.is_file() and generated_markdown(view.read_text(encoding="utf-8")):
                delete.append(view)
            if not view.exists() or view in delete:
                converted_views[name] = Path(name).stem + ".json"
        page = trip / "map/index.html"
        if page.is_file():
            text = page.read_text(encoding="utf-8")
            if "const BASE =" in text and "const DATA =" in text and "id=\"view-days\"" in text:
                delete.append(page)
        if (trip / "map/config.json").is_file() and (page in delete or not page.exists()):
            converted_views["map/index.html"] = "map/config.json"
        # 紀錄的文字欄位也會寫連結（例如行程備註連到 bookings.md），和 Markdown 一樣改到紀錄檔。
        for path in record_paths(trip):
            text = updates.get(path) or path.read_text(encoding="utf-8")
            new = update_view_links(text, path.parent.relative_to(trip).as_posix(), converted_views)
            if new != text:
                updates[path] = new
        for path in trip.rglob("*.md"):
            if path in delete:
                continue
            text = path.read_text(encoding="utf-8")
            new = text.replace("python3 core/trips.py build", "seasoningway render")
            new = update_view_links(new.replace("seasoningway build", "seasoningway render"),
                                    path.parent.relative_to(trip).as_posix(), converted_views)
            if new != text:
                updates[path] = new
    changed = []
    for path, text in updates.items():
        write_text(path, text)
        changed.append(path.relative_to(root).as_posix())
    for source, target in moves:
        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)
        changed.append(target.relative_to(root).as_posix())
    for path in delete:
        if path.is_file() and (path.suffix == ".html" or generated_markdown(path.read_text(encoding="utf-8"))):
            path.unlink()
            changed.append(path.relative_to(root).as_posix())
    attrs = root / ".gitattributes"
    if attrs.is_file():
        text = attrs.read_text(encoding="utf-8")
        kept = [line for line in text.splitlines() if next(iter(line.split()), None) not in OLD_ATTRIBUTES]
        trimmed = "\n".join(line for line in kept if line != "# 第 1 階段沿用的產物標記。").rstrip() + "\n"
        if trimmed != text:
            write_text(attrs, trimmed)
            changed.append(attrs.name)
    if ensure_out_ignored(root):
        changed.append(".gitignore")
    return changed


def upgrade_v5(root: Path) -> list[str]:
    """將地圖預排行程合併到逐日行程，保留既有安排。"""
    updates = {}
    changed = []
    for path in sorted((root / TRIPS).glob("*/map/config.json")):
        trip = path.parents[1]
        rel = path.relative_to(root).as_posix()
        if not trip.resolve().is_relative_to(root.resolve()) or not path.resolve().is_relative_to(trip.resolve()):
            raise Problem(f"{rel}：地圖設定指向旅程外")
        cfg = parse_json(path.read_text(encoding="utf-8"), rel)
        if not isinstance(cfg, dict):
            raise Problem(f"{rel}：紀錄要是 JSON 物件")
        if "preset" not in cfg:
            continue
        preset = cfg.pop("preset")
        if not isinstance(preset, dict) or any(
            not real_date(day) or not isinstance(ids, list) or any(not isinstance(pid, str) for pid in ids)
            for day, ids in preset.items()
        ):
            raise Problem(f"{rel}：preset 應為日期對應地點 id 清單")

        if any(preset.values()):
            itinerary_path = trip / "itinerary.json"
            itinerary_rel = itinerary_path.relative_to(root).as_posix()
            if not itinerary_path.is_file() or not itinerary_path.resolve().is_relative_to(trip.resolve()):
                raise Problem(f"{itinerary_rel}：合併 preset 需要旅程內的行程檔")
            itinerary = parse_json(itinerary_path.read_text(encoding="utf-8"), itinerary_rel)
            days = itinerary.get("days") if isinstance(itinerary, dict) else None
            if not isinstance(days, list) or any(
                not isinstance(day, dict) or not isinstance(day.get("date"), str) or not real_date(day["date"])
                or not isinstance(day.get("places", []), list)
                or any(not isinstance(pid, str) for pid in day.get("places", []))
                for day in days
            ):
                raise Problem(f"{itinerary_rel}：days 應為含日期及可選地點清單的逐日紀錄")
            by_date = {day["date"]: day for day in days}
            scheduled = {pid for day in days for pid in day.get("places", [])}
            count, original_days = 0, len(days)
            for iso, ids in sorted(preset.items()):
                for pid in ids:
                    if pid in scheduled:
                        continue
                    if iso not in by_date:
                        by_date[iso] = {"date": iso}
                        days.append(by_date[iso])
                    by_date[iso].setdefault("places", []).append(pid)
                    scheduled.add(pid)
                    count += 1
            if count:
                if len(days) > original_days:
                    days.sort(key=lambda day: day["date"])
                updates[itinerary_path] = dump_json(itinerary)
                changed.append(f"{itinerary_rel}：併入 {count} 個預排地點")

        updates[path] = dump_json(cfg)
        changed.append(f"{rel}：移除 preset")
    for path, text in updates.items():
        write_text(path, text)
    return changed
