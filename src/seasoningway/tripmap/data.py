"""讀地點、地圖設定、逐日行程和旅程日期，整理成地圖頁內嵌的資料。"""
import json
import re
from pathlib import Path
from urllib.parse import urlparse

from ..common import Problem, real_date

from ..records import parse_front_matter, read_record, record_problems, ref_problems
DEFAULT_TEXTS = {
    "drive_note": "開車路線與時數沿 OpenStreetMap 的主要道路粗估，不含塞車和天候；實際以每天的「Google 地圖 ↗」為準，"
                  "中途點超過 3 個時請用 App 開。停留時間和推薦度是 Claude 的估計。",
    "lodging_note": "放具體的旅館和酒店；想加哪一家，把名字告訴 Claude。",
    "region_word": "地區",
}


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class MapTrip:
    """一趟旅程的地圖頁輸入。資料有問題就丟 Problem（訊息和 check 的一樣）。"""

    def __init__(self, trip_dir: Path):
        self.dir = trip_dir.resolve()
        self.map = self.dir / "map"
        self.cache = self.dir / "cache"
        root = self.dir.parents[1]  # repo 根目錄（trips/<旅程> 往上兩層）
        problems = []
        sources = [self.map / "config.json", *sorted((self.dir / "places").glob("*.json"))]
        for path in sources:
            problems += record_problems(path.relative_to(root).as_posix(), path.read_text(encoding="utf-8"))
        meta = parse_front_matter((self.dir / "README.md").read_text(encoding="utf-8")) or {}
        if not meta.get("start") or not meta.get("end"):
            problems.append(f"{self.dir.relative_to(root).as_posix()}/README.md 開頭要有 start、end（地圖頁的日期）")
        else:
            start, end = real_date(meta["start"]), real_date(meta["end"])
            if start is None or end is None or end < start:
                problems.append(f"{self.dir.relative_to(root).as_posix()}/README.md：地圖的日期區間錯誤")
        if problems:
            raise Problem("\n".join(problems))
        self.config = load_json(self.map / "config.json")
        self.places = [load_json(p) for p in sources[1:]]
        self.start, self.end = str(meta["start"]), str(meta["end"])
        self.by_id = {p["id"]: p for p in self.places}

    def optional(self, name: str, default=None, *, cached=False):
        path = (self.cache if cached else self.map) / name
        return load_json(path) if path.exists() else default


def map_query(place: dict, cfg: dict) -> str | None:
    """地圖 App 搜尋這個點的文字：原文名稱＋地區，Google 才會顯示景點名而不是地址。
    地點的 map_query 可以指定（名稱有兩個地方、太長），false 表示用座標（泛指一類的店、列車），回傳 None。"""
    q = place.get("map_query")
    if q is False:
        return None
    if isinstance(q, str):
        return q
    name = re.sub(r"【.*?】", "", place.get("local_name", place["name"]))
    name = re.sub(r"[（(](.*?)[）)]", r" \1", name)
    region = place["region"].split("・")[0]
    for old, new in cfg.get("search", {}).get("region_replace", []):
        region = region.replace(old, new)
    return re.sub(r"\s+", " ", f"{name} {region}").strip()


def dur_text(m: int) -> str:
    """分鐘寫成「45 分鐘」「1.5 小時」「1 小時 15 分鐘」（template.html 的 durText 同一套寫法）。"""
    if m < 60:
        return f"{m} 分鐘"
    return f"{m / 60:g} 小時" if m % 30 == 0 else f"{m // 60} 小時 {m % 60} 分鐘"


def visit_text(v: dict) -> str:
    """預計停留時間：「1–1.5 小時」「30–45 分鐘」「45 分鐘–1 小時」。"""
    a, b = v["min"], v["max"]
    if a == b:
        return dur_text(a)
    if b < 60:
        return f"{a}–{b} 分鐘"
    if a >= 60 and a % 30 == 0 and b % 30 == 0:
        return f"{a / 60:g}–{b / 60:g} 小時"
    return f"{dur_text(a)}–{dur_text(b)}"


def links(sources: list[dict]) -> list[dict]:
    """頁面上的來源連結 {t, u}：只放網址（研究筆記的相對路徑在 artifact 上打不開）；沒有標題的用網域。"""
    return [{"t": s.get("title") or urlparse(s["url"]).netloc, "u": s["url"]} for s in sources if s["url"].startswith("http")]


def page_spot(p: dict, cfg: dict, place_ids: dict, photos: dict) -> dict:
    """一筆地點 → 頁面用的精簡欄位（template.html 讀這些名稱）。"""
    s = {"id": p["id"], "name": p["name"], "local": p.get("local_name", p["name"]), "region": p["region"],
         "lat": p["location"]["lat"], "lon": p["location"]["lon"], "cats": p["categories"], "summary": p["summary"]}
    if "short_name" in p:
        s["label"] = p["short_name"]
    if p["location"].get("approximate"):
        s["approx"] = True
    if "detail" in p:
        s["detail"] = p["detail"]
    season = p.get("season", {})
    for key, out in (("tier", "tier"), ("why", "tier_why"), ("note", "season_note")):
        if key in season:
            s[out] = season[key]
    facts = [[f["label"], f["value"]] for f in p.get("facts", [])]
    contact = p.get("contact", {})
    facts += [[label, contact[key]] for key, label in (("address", "地址"), ("phone", "電話")) if key in contact]
    if facts:
        s["facts"] = facts
    order = {r["site"]: i for i, r in enumerate(cfg.get("ratings", []))}
    ratings = sorted((r for r in p.get("ratings", []) if r["site"] in order), key=lambda r: order[r["site"]])
    if ratings:
        s["ratings"] = ratings
    s["sources"] = links(p.get("sources", []))
    if "website" in p:
        s["website"] = p["website"]
    if "visit" in p:
        v = p["visit"]
        s["visit"] = {"min": v["min"], "max": v["max"], "note": v["note"], "sources": links(v["sources"])}
    if "lodging" in p:
        s["lodging"] = p["lodging"]["area"]
    if "rental" in p:
        s["rental"] = p["rental"]
    q = map_query(p, cfg)
    if q:
        s["gq"] = q
        if p["id"] in place_ids:
            s["gpid"] = place_ids[p["id"]]
    if p["id"] in photos:
        s["photo"] = photos[p["id"]]
    return s


def page_data(t: MapTrip) -> dict:
    """頁面內嵌的 DATA：地點、類別、旅程骨架、開車參數、頁面文字。"""
    cfg = t.config
    place_ids = t.optional("place_ids.json", {"ids": {}}, cached=True)["ids"]
    photos = t.optional("photos.json", {})
    roads = t.optional("roads.json", cached=True)
    drive = cfg["drive"]
    known = {c["id"] for c in cfg["categories"]}
    unknown = sorted({c for p in t.places for c in p["categories"]} - known)
    if unknown:
        raise Problem(f"{t.map.name}/config.json 的 categories 沒有定義：{'、'.join(unknown)}（地點用到的類別都要列）")
    home = cfg["home"]
    if home not in t.by_id:
        raise Problem(f"map/config.json 指到不存在的地點 {home}（places/{home}.json）")
    root = t.dir.parents[1]
    itinerary_path = t.dir / "itinerary.json"
    itinerary = read_record(root, itinerary_path)
    problems = ref_problems(root, itinerary_path.relative_to(root).as_posix(), "itinerary", itinerary)
    if problems:
        raise Problem("\n".join(problems))
    preset = {
        day["date"]: {target: day[source] for source, target in (("places", "ids"), ("stay_place", "stay")) if source in day}
        for day in itinerary["days"]
    }
    dates = sorted(p["as_of"] for p in t.places)
    lat = [p["location"]["lat"] for p in t.places]
    lon = [p["location"]["lon"] for p in t.places]
    view = cfg.get("view") or {"lat": round((min(lat) + max(lat)) / 2, 2), "lon": round((min(lon) + max(lon)) / 2, 2), "zoom": 6}
    straight = drive.get("straight", {"express": [1.2, 80], "local": [1.35, 50], "express_km": 40})
    network = drive.get("network", {"speed": [80, 55, 50, 45, 35, 25], "link": 45})
    return {
        "as_of": f"{dates[0]}～{dates[-1]}" if dates else "",
        "spots": [page_spot(p, cfg, place_ids, photos) for p in t.places],
        "cats": [[c["id"], c["glyph"], c["name"]] for c in cfg["categories"]],
        "trip": {"start": t.start, "end": t.end, "home": home, "holidays": cfg.get("holidays", {}),
                 "notes": cfg.get("notes", {}), "preset": preset},
        "road": {"express": straight["express"], "local": straight["local"], "express_km": straight["express_km"], "net": network},
        "max_h": drive["max_hours"],
        "roads": {"asof": roads["asof"], "snap": roads["snap"]} if roads else None,
        "page": {
            "short": cfg["short_title"], "storage": cfg["storage_prefix"], "view": view,
            "region_order": cfg.get("region_order", []), "season": cfg["season"]["label"], "tiers": cfg["season"]["tiers"],
            "ratings": cfg.get("ratings", []), "credits": cfg.get("basemap", {}).get("credits", []),
            "texts": {**DEFAULT_TEXTS, **cfg.get("texts", {})},
        },
    }
