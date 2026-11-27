"""把頁面資料（data.page_data）、行政區界（map/regions.json）、底圖圖磚清單（cache/base/）合成 index.html。"""
import datetime
import html
import json
import math
import re
from dataclasses import dataclass

from importlib.resources import files
from pathlib import PurePosixPath

from ..common import Problem

from .data import MapTrip

ASSETS = files(__package__)


def centroid(ring):
    a = cx = cy = 0
    for (x0, y0), (x1, y1) in zip(ring, ring[1:]):
        c = x0 * y1 - x1 * y0; a += c; cx += (x0 + x1) * c; cy += (y0 + y1) * c
    a /= 2
    return (cx / (6 * a), cy / (6 * a), abs(a)) if a else (ring[0][0], ring[0][1], 0)


def dates_text(start: str, end: str) -> str:
    """「2099 年 1 月 3 日至 8 日，共 6 天。」；跨月、跨年寫完整。"""
    a, b = datetime.date.fromisoformat(start), datetime.date.fromisoformat(end)
    head = f"{a.year} 年 {a.month} 月 {a.day} 日至"
    tail = f" {b.day} 日" if (a.year, a.month) == (b.year, b.month) else \
        f" {b.month} 月 {b.day} 日" if a.year == b.year else f" {b.year} 年 {b.month} 月 {b.day} 日"
    return f"{head}{tail}，共 {(b - a).days + 1} 天。"


def credits_html(credits: list[dict]) -> str:
    """頁尾出處：地圖設定 basemap.credits 列的資料（引擎畫底圖固定用的 OSM、Protomaps、山影寫在模板裡）。"""
    out = ""
    for c in credits:
        src = f'<a href="{html.escape(c["url"])}">{html.escape(c["source"])}</a>' if c.get("url") else html.escape(c["source"])
        out += f'<span class="sep" aria-hidden="true">・</span>{html.escape(c["what"])} 出典：{src}'
    return out


def script_json(value, compact: bool = False) -> str:
    """放進 inline <script> 的 JSON。< > & 轉成 \\u 跳脫：地點的文字可能抄自網頁，含 </script> 會跳出 script 執行。"""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":") if compact else None)
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


@dataclass
class MapPage:
    trip: MapTrip
    data: dict
    geo: dict
    labels: list[dict]
    base: dict
    road_data: str | None


def load_page(t: MapTrip, data: dict) -> MapPage:
    """驗證地圖來源與快取並整理頁面所需資料。"""
    where = f"trips/{t.dir.name}/map"
    for parent in (t.map, t.cache):
        if not parent.resolve().is_relative_to(t.dir):
            raise Problem(f"{where}：地圖來源或快取目錄指向旅程外")
    required = (t.map / "regions.json", t.cache / "base/tiles.json", t.cache / "base/places.json")
    missing = [p.relative_to(t.dir).as_posix() for p in required if not p.is_file()]
    roads_path = t.cache / "roads.txt"
    if roads_path.is_symlink():
        raise Problem(f"{where}：cache/roads.txt 不能是檔案連結")
    has_road_data = roads_path.is_file()
    has_road_metadata = (t.cache / "roads.json").is_file()
    if has_road_data != has_road_metadata:
        missing.append("cache/roads.txt" if has_road_metadata else "cache/roads.json")
    if missing:
        raise Problem(f"trips/{t.dir.name}/ 還缺 {'、'.join(missing)}，地圖頁產生不了。見 docs/tripmap.md。")
    geo = json.loads((t.map / "regions.json").read_text(encoding="utf-8"))
    if not isinstance(geo.get("features"), list):
        raise Problem(f"{where}/regions.json：features 要是陣列")
    labels = []
    for f in geo["features"]:
        best = max((centroid(p[0]) for p in f["geometry"]["coordinates"]), key=lambda c: c[2])
        labels.append({"zh": f["properties"]["zh"], "lon": round(best[0], 3), "lat": round(best[1], 3), "focus": f["properties"].get("focus", False)})
    # 底圖：base/tiles.json（basemap_tiles.py 產生）＝外圍、細緻兩層圖磚每個縮放的範圍，和有哪些 4×4 拼成的大圖（全是海的沒存）
    tiles = json.loads((t.cache / "base" / "tiles.json").read_text(encoding="utf-8"))
    zs = sorted(int(z) for z in tiles["ranges"])
    if not zs or any(not 0 <= z <= 30 for z in zs):
        raise Problem(f"{where}：cache/base/tiles.json 的縮放範圍無效")
    fine = tiles["fine_min"]
    if type(fine) is not int or fine not in zs or fine - 1 not in zs:
        raise Problem(f"{where}：cache/base/tiles.json 缺少細緻或外圍縮放資料")
    if not isinstance(tiles["have"], dict) or type(tiles["atlas"]) is not int or tiles["atlas"] < 1:
        raise Problem(f"{where}：cache/base/tiles.json 的圖磚索引格式錯誤")
    for zoom, values in tiles["have"].items():
        if zoom not in tiles["ranges"] or not isinstance(values, list):
            raise Problem(f"{where}：cache/base/tiles.json 的圖磚索引格式錯誤")
        for value in values:
            if not isinstance(value, str) or not re.fullmatch(r"\d+/\d+", value):
                raise Problem(f"{where}：cache/base/tiles.json 的圖磚索引格式錯誤")
            for theme in ("light", "dark"):
                relative = f"base/tiles/{theme}/{zoom}/{value.replace('/', '-')}.webp"
                if not (t.cache / relative).is_file():
                    raise Problem(f"{where}：缺少 cache/{relative}")

    def bounds(z):
        x0, x1, y0, y1 = tiles["ranges"][str(z)]
        if not all(type(v) is int and 0 <= v < 2 ** z for v in (x0, x1, y0, y1)) or x1 < x0 or y1 < y0:
            raise Problem(f"{where}：cache/base/tiles.json 的 bounds 格式錯誤")
        lat = lambda y: math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / 2 ** z))))
        # 四捨五入：各平台的數學函式庫最後一位可能不同（Windows 和 Linux 產生的頁面要一樣）
        return {k: round(v, 6) for k, v in {"north": lat(y0), "south": lat(y1 + 1), "west": x0 / 2 ** z * 360 - 180,
                                             "east": (x1 + 1) / 2 ** z * 360 - 180}.items()}
    base = {"fine": bounds(zs[-1]), "wide": bounds(tiles["fine_min"] - 1),
            "tiles": {"date": tiles["date"], "fine_min": tiles["fine_min"], "max": zs[-1], "atlas": tiles["atlas"],
                      "ranges": tiles["ranges"], "have": tiles["have"]}}
    # 地名（base/places.json，basemap_tiles.py 產生）：圖磚裡不畫字，地圖頁照分級畫
    base["places"] = json.loads((t.cache / "base" / "places.json").read_text(encoding="utf-8"))
    if not isinstance(base["places"], list):
        raise Problem(f"{where}：cache/base/places.json 要是陣列")
    for spot in data["spots"]:
        if (photo := spot.get("photo")) is not None:
            relative = photo["file"]
            path = PurePosixPath(relative)
            if (not path.parts or path.parts[0] != "photos" or ".." in path.parts or "\\" in relative
                    or not (t.map / relative).is_file()):
                raise Problem(f"{where}：{spot['id']} 的照片要指向 map/photos/ 的檔案")
    road_data = roads_path.read_text(encoding="utf-8").strip() if has_road_data else None
    return MapPage(t, data, geo, labels, base, road_data)


def render(page: MapPage) -> str:
    """用驗證過的地圖資料產生 index.html。"""
    t, data, geo, labels, base = page.trip, page.data, page.geo, page.labels, page.base
    cfg = t.config
    texts = data["page"]["texts"]
    slots = {f"<!--__{key}__-->": html.escape(value, quote=False) for key, value in {
        "TITLE": cfg["title"], "HEADING": cfg.get("heading", cfg["short_title"] + "：我的行程"),
        "DATES": dates_text(t.start, t.end), "DRIVE_NOTE": texts["drive_note"],
        "RATINGS_NOTE": cfg.get("ratings_note", ""),
    }.items()}
    slots["<!--__CREDITS__-->"] = credits_html(data["page"]["credits"])
    slots["/*__LEAFLET_CSS__*/"] = (ASSETS / "leaflet-1.9.4.css").read_text(encoding="utf-8")
    slots |= {"/*__DATA__*/null": script_json(data), "/*__GEO__*/null": script_json(geo, compact=True),
              "/*__LABELS__*/null": script_json(labels), "/*__BASE__*/null": script_json(base, compact=True),
              "/*__ROADS__*/null": script_json(page.road_data)}
    # 一次換完：分次 replace 的話，前面塞進去的文字裡如果有後面的佔位符，也會被換掉
    page = (ASSETS / "template.html").read_text(encoding="utf-8")
    return re.sub("|".join(map(re.escape, slots)), lambda m: slots[m[0]], page)
