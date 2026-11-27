"""把第 3 版資料升級到第 4 版。"""
import json
import re
from pathlib import Path

from .common import Problem, TRIPS
from .files import write_text
from .published import publication_paths
from .records import dump_json, parse_json, record_kind, record_paths, schema_url

CACHE_FILES = ("roads.txt", "roads.json", "place_ids.json", "base")
VIEWS = ("budget.md", "bookings.md", "itinerary.md")
GENERATED_START = "<!-- 由 seasoningway 產生"
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
        converted_views = []
        for name in VIEWS:
            view = trip / name
            if not view.exists():
                converted_views.append(name)
            elif view.is_file() and view.read_text(encoding="utf-8").startswith(GENERATED_START):
                converted_views.append(name)
                delete.append(view)
        page = trip / "map/index.html"
        if page.is_file():
            text = page.read_text(encoding="utf-8")
            if "const BASE =" in text and "const DATA =" in text and "id=\"view-days\"" in text:
                delete.append(page)
        for path in trip.rglob("*.md"):
            if path in delete:
                continue
            text = path.read_text(encoding="utf-8")
            new = text.replace("seasoningway build", "seasoningway render")
            for name in converted_views:
                record = f"{Path(name).stem}.json"
                prefix = "" if path.parent == trip else "../" if path.parent.parent == trip else None
                if prefix is None:
                    continue
                new = new.replace(f"[{name}]({prefix}{name})", f"[{record}]({prefix}{record})")  # 檔名當連結文字的一起改
                new = new.replace(f"]({prefix}{name})", f"]({prefix}{record})")
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
        if path.is_file() and (path.suffix == ".html" or path.read_text(encoding="utf-8").startswith(GENERATED_START)):
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
