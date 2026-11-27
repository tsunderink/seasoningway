"""畫地圖頁的底圖圖磚：OpenStreetMap 資料（Protomaps 每日建置）＋ AWS Terrain Tiles 山影＋國土數值情報的鐵路。

目前只支援日本：鐵路讀國土數值情報 N02、地名取 name:ja、排除東京 23 區；其他國家要換掉 rail()、places() 的這幾處
（做法和授權見 docs/roadmap.md「之後的方向」）。範圍、縮放取地圖設定 map/config.json 的 basemap，
只畫 map/regions.json（行政區界，GeoJSON，properties 有 zh＝名稱、focus＝是不是重點地區）範圍內的地名。

準備（只有重畫底圖時才需要）：
  工作資料夾裡 npm i maplibre-gl@5 @protomaps/basemaps@5
  pip install pmtiles shapely requests pillow numpy；apt-get install fonts-noto-cjk（日文地名用）
  國土數值情報 N02 鐵道（2024 年版）下載解壓
執行：
  python3 -m seasoningway.tripmap.basemap_tiles trips/<旅程> <工作資料夾> <N02-24_RailroadSection.geojson> [Protomaps 建置日期 YYYYMMDD]
輸出 cache/base/tiles/{light,dark}/{z}/{ax}-{ay}.webp（每張是 4×4 個圖磚拼成的大圖）、cache/base/tiles.json（範圍、有哪些大圖、資料日期）
和 cache/base/places.json（地名：圖磚裡不畫字，地圖頁照分級另外畫），render.py 會讀這兩個 json。

做法：本機開一個伺服器，把遠端 PMTiles（range 讀取）、地形圖磚、字型快取到工作資料夾；
basemap_tiles_render.mjs 用無頭 Chromium 打開 basemap_tiles.html（配色、圖層在那裡改），一次畫一大塊（6×6 張加四周 256px），
再切成 512px 的圖磚（螢幕上顯示成 256px，iPad、手機上字才清楚），每 4×4 張拼成一張大圖：artifact 一個版本最多 512 個檔，一張一檔放不下。
大圖右邊、下面多留 4px 鄰居的內容，地圖頁每張圖磚多蓋 0.6px 時才有東西可蓋（分數縮放時圖磚之間不露縫）。全是海的大圖不存，地圖頁直接露出背景色。
外圍（拉遠看全程用）預設 z4–z7，細緻（景點所在的地區）z8–z10；放大超過細緻的最大縮放時，地圖頁拿最大的那層放大顯示。"""
import functools, gzip, json, math, os, shutil, subprocess, sys, threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from importlib.resources import as_file, files
from pathlib import Path

import numpy as np
import requests
from PIL import Image
from pmtiles.reader import Reader

from ..common import Problem
from .data import MapTrip

ASSETS = files(__package__)    # basemap_tiles.html、basemap_tiles_render.mjs
MAP = BASE_DIR = WIDE = FINE = ZOOMS = None  # main() 依地圖設定填：旅程的 map/、外圍和細緻的範圍 [基準縮放, x 起, x 迄, y 起, y 迄]、各自的縮放
N, MG = 6, 256                  # 一塊 6×6 張，四周多畫 256px（邊上的字和線才不會被切掉）
THEMES = ("light", "dark")
A, APRON = 4, 4                 # 大圖：4×4 張圖磚，右邊、下面多 4px
SEA = {"light": (211, 225, 235), "dark": (14, 27, 37)}  # 和 basemap_tiles.html、template.html 的海色一樣


def tile_range(base, z):
    bz, x0, x1, y0, y1 = base
    if z <= bz:
        k = 2 ** (bz - z); return x0 // k, x1 // k, y0 // k, y1 // k
    k = 2 ** (z - bz); return x0 * k, (x1 + 1) * k - 1, y0 * k, (y1 + 1) * k - 1


def rail(src, out):
    """國土數值情報 N02：新幹線、JR 在來線、其他普通鐵道，合併簡化成三條多段線。"""
    from shapely.geometry import shape, mapping, box
    from shapely.ops import linemerge, unary_union
    bz, x0, x1, y0, y1 = WIDE
    lat = lambda y: math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / 2 ** bz))))
    area = box(x0 / 2 ** bz * 360 - 180, lat(y1 + 1), (x1 + 1) / 2 ** bz * 360 - 180, lat(y0))
    groups = {"shinkansen": [], "jr": [], "other": []}
    for f in json.load(open(src, encoding="utf-8"))["features"]:
        p = f["properties"]
        if p["N02_002"] == "1": k = "shinkansen"
        elif p["N02_001"] not in ("11", "12"): continue  # 只留普通鐵道（不含纜車、路面電車、單軌）
        else: k = "jr" if p["N02_002"] == "2" else "other"
        g = shape(f["geometry"])
        if g.intersects(area): groups[k].append(g)
    feats = [{"type": "Feature", "properties": {"kind": k}, "geometry": mapping(linemerge(unary_union(gs)).simplify(0.0003))} for k, gs in groups.items()]
    json.dump({"type": "FeatureCollection", "features": feats}, open(out, "w", newline="\n"), separators=(",", ":"))


def places(url):
    """從 OSM 資料取地名（市、町），給地圖頁畫：細緻範圍用 z9 圖磚裡的全部市町，外圍用 z6 的縣廳所在地和大城市。
    分級 t：1＝縣廳所在地和東京、2＝人口 10 萬以上的市、3＝其他的市、4＝町；不要村和東京 23 區（太擠）。"""
    import mapbox_vector_tile
    from shapely.geometry import shape, Point
    from shapely.ops import unary_union
    japan = unary_union([shape(f["geometry"]).buffer(0.08) for f in json.loads((MAP / "regions.json").read_text(encoding="utf-8"))["features"]])
    out = {}
    for base, z in ((WIDE, 6), (FINE, 9)):
        x0, x1, y0, y1 = tile_range(base, z)
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                r = requests.get(f"{url}/vt/{z}/{x}/{y}.mvt", timeout=120)
                if r.status_code != 200 or not r.content: continue
                L = mapbox_vector_tile.decode(r.content, default_options={"y_coord_down": True}).get("places", {})
                ext = L.get("extent", 4096)
                for ft in L.get("features", []):
                    p = ft["properties"]
                    if p.get("kind") != "locality" or p.get("kind_detail") not in ("city", "town"): continue
                    name = p.get("name:ja") or p.get("name") or ""
                    if not name or name.endswith("区"): continue
                    cap, pop = p.get("capital") in ("yes", "4"), p.get("population") or 0
                    t = 1 if cap else 4 if p["kind_detail"] == "town" else 2 if pop >= 100000 else 3
                    if z == 6 and t > 2: continue
                    gx, gy = ft["geometry"]["coordinates"]
                    lon = (x + gx / ext) / 2 ** z * 360 - 180
                    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + gy / ext) / 2 ** z))))
                    if not japan.contains(Point(lon, lat)): continue  # 只要 regions.json 範圍內的
                    key = p.get("wikidata") or name
                    if key in out and out[key]["z"] >= z: continue
                    out[key] = {"n": "東京" if name == "東京都" else name, "lat": round(lat, 4), "lon": round(lon, 4), "t": t, "p": pop, "z": z}
    rows = sorted(out.values(), key=lambda r: (r["t"], -r["p"]))
    for r in rows: del r["z"]
    (BASE_DIR / "places.json").write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8", newline="\n")
    print(len(rows), "個地名", {t: sum(r["t"] == t for r in rows) for t in (1, 2, 3, 4)})


def serve(work, node_modules, pmtiles_url):
    cache = work / "cache"; sess = requests.Session()

    @functools.lru_cache(maxsize=8192)
    def rng(off, ln):
        for _ in range(5):
            try:
                r = sess.get(pmtiles_url, headers={"Range": f"bytes={off}-{off + ln - 1}"}, timeout=60)
                if r.status_code == 206 and len(r.content) == ln: return r.content
            except requests.RequestException as e:
                print("重試", e, file=sys.stderr)
        raise IOError("PMTiles 讀取失敗")
    reader = Reader(rng)

    def cached(path, fetch):
        if path.exists(): return path.read_bytes()
        data = fetch()
        if data is None: return None
        path.parent.mkdir(parents=True, exist_ok=True); tmp = path.with_suffix(".tmp"); tmp.write_bytes(data); tmp.replace(path)
        return data

    def remote(url):
        def f():
            for _ in range(4):
                try:
                    r = sess.get(url, timeout=60)
                    if r.status_code == 200: return r.content
                    if r.status_code == 404: return None
                except requests.RequestException as e:
                    print("重試", e, file=sys.stderr)
            return None
        return f

    def vt(z, x, y):
        def f():
            t = reader.get(z, x, y)
            return b"" if t is None else gzip.decompress(t) if t[:2] == b"\x1f\x8b" else t
        return f

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def send(self, data, ctype):
            if data is None: self.send_response(404); self.end_headers(); return
            if not data: self.send_response(204); self.end_headers(); return
            self.send_response(200); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(data)))
            self.end_headers(); self.wfile.write(data)

        def do_GET(self):
            p = requests.utils.unquote(self.path.split("?")[0]).strip("/"); parts = p.split("/")
            try:
                if p == "basemap_tiles.html": return self.send((ASSETS / p).read_bytes(), "text/html; charset=utf-8")
                if p == "rail.geojson": return self.send((work / p).read_bytes(), "application/json")
                if parts[0] == "node_modules" and ".." not in parts:
                    f = node_modules.joinpath(*parts[1:]); return self.send(f.read_bytes() if f.is_file() else None, "text/css" if p.endswith(".css") else "text/javascript")
                if parts[0] == "vt":
                    z, x, y = int(parts[1]), int(parts[2]), int(parts[3].split(".")[0])
                    return self.send(cached(cache / f"vt/{z}/{x}/{y}.mvt", vt(z, x, y)), "application/x-protobuf")
                if parts[0] == "dem":
                    z, x, y = (int(v.split(".")[0]) for v in parts[1:4])
                    return self.send(cached(cache / f"dem/{z}/{x}/{y}.png", remote(f"https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png")), "image/png")
                if parts[0] in ("fonts", "sprites") and ".." not in parts:
                    rest = "/".join(parts[1:])
                    ctype = "application/json" if rest.endswith(".json") else "image/png" if rest.endswith(".png") else "application/x-protobuf"
                    return self.send(cached(cache / parts[0] / rest, remote(f"https://protomaps.github.io/basemaps-assets/{parts[0]}/{requests.utils.quote(rest)}")), ctype)
            except Exception as e:
                print("錯誤", p, e, file=sys.stderr); self.send_response(500); self.end_headers(); return
            self.send(None, "")

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}"


def main(argv, *, output: Path | None = None):
    global MAP, BASE_DIR, WIDE, FINE, ZOOMS
    if len(argv) < 3:
        print(__doc__); return 1
    t = MapTrip(Path(argv[0]))
    b = t.config["basemap"]
    MAP, WIDE, FINE = t.map, tuple(b["wide"]), tuple(b["fine"])
    if not (MAP / "regions.json").is_file():
        raise Problem("底圖需要 map/regions.json")
    BASE_DIR = output if output is not None else t.cache / "base"
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    wz, fz = b.get("wide_zooms", [4, 7]), b.get("fine_zooms", [8, 10])
    ZOOMS = {"wide": range(wz[0], wz[1] + 1), "fine": range(fz[0], fz[1] + 1)}
    work_root, n02 = Path(argv[1]).resolve(), argv[2]
    build = argv[3] if len(argv) > 3 else "20261118"
    work = work_root / build / t.dir.name
    work.mkdir(parents=True, exist_ok=True)
    rail(n02, work / "rail.geojson")
    url = serve(work, work_root / "node_modules", f"https://build.protomaps.com/{build}.pmtiles")
    jobs = []
    for part, zs in ZOOMS.items():
        for z in zs:
            x0, x1, y0, y1 = tile_range(WIDE if part == "wide" else FINE, z)
            for cx in range(x0, x1 + 1, N):
                for cy in range(y0, y1 + 1, N):
                    ws = 256 * 2 ** z; px, py = (cx + N / 2) * 256, (cy + N / 2) * 256
                    lon, lat = px / ws * 360 - 180, math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * py / ws))))
                    for th in THEMES:
                        out = work / "chunks" / f"{th}-{z}-{cx}-{cy}.png"
                        jobs.append({"theme": th, "z": z, "lon": lon, "lat": lat, "size": N * 256 + 2 * MG, "out": str(out)})
    jobs.sort(key=lambda j: j["theme"])  # 同一種配色連著畫，不用一直重建地圖
    (work / "chunks").mkdir(exist_ok=True)
    (work / "jobs.json").write_text(json.dumps(jobs), newline="\n")
    node_paths = [str(work_root / "node_modules")]
    if os.environ.get("NODE_PATH"):
        node_paths.append(os.environ["NODE_PATH"])
    with as_file(ASSETS / "basemap_tiles_render.mjs") as script:
        subprocess.run(["node", str(script), url, str(work / "jobs.json")], check=True,
                       env={**os.environ, "NODE_PATH": os.pathsep.join(node_paths)})
    places(url)
    out = BASE_DIR / "tiles"
    if out.exists(): shutil.rmtree(out)
    out.mkdir(parents=True)
    have, total = {}, 0
    for part, zs in ZOOMS.items():
        for z in zs:
            x0, x1, y0, y1 = tile_range(WIDE if part == "wide" else FINE, z)
            cache = {}

            def tile(th, x, y):
                """從畫好的大塊切出一張 512px 圖磚；範圍外（最多多一張，在大塊的四周）照樣切得到，再外面回傳 None。"""
                if not (x0 <= x <= x1 + 1 and y0 <= y <= y1 + 1): return None
                cx = x0 + min((x - x0) // N, (x1 - x0) // N) * N; cy = y0 + min((y - y0) // N, (y1 - y0) // N) * N
                k = (th, cx, cy)
                if k not in cache:
                    if len(cache) > 6: cache.clear()
                    cache[k] = Image.open(work / "chunks" / f"{th}-{z}-{cx}-{cy}.png").convert("RGB")
                bx = ((MG + (x - cx) * 256) * 2, (MG + (y - cy) * 256) * 2)
                return cache[k].crop(bx + (bx[0] + 512, bx[1] + 512))

            for ax in range((x1 - x0) // A + 1):
                for ay in range((y1 - y0) // A + 1):
                    ims = {}
                    for th in THEMES:
                        im = Image.new("RGB", (A * 512 + APRON, A * 512 + APRON), SEA[th])
                        for i in range(A + 1):
                            for j in range(A + 1):
                                t = tile(th, x0 + ax * A + i, y0 + ay * A + j)
                                if t is not None: im.paste(t.crop((0, 0, 512 if i < A else APRON, 512 if j < A else APRON)), (i * 512, j * 512))
                        ims[th] = im
                    if all(np.ptp(np.asarray(im).reshape(-1, 3), axis=0).max() <= 3 for im in ims.values()): continue  # 全是海
                    for th, im in ims.items():
                        f = out / th / str(z) / f"{ax}-{ay}.webp"; f.parent.mkdir(parents=True, exist_ok=True)
                        im.save(f, "WEBP", quality=78, method=6); total += f.stat().st_size
                    have.setdefault(str(z), []).append(f"{ax}/{ay}")
    ranges = {}
    for part, zs in ZOOMS.items():
        for z in zs: ranges[str(z)] = tile_range(WIDE if part == "wide" else FINE, z)
    meta = {"source": f"Protomaps {build}", "date": f"{build[:4]}-{build[4:6]}-{build[6:]}", "fine_min": min(ZOOMS["fine"]),
            "atlas": A, "ranges": ranges, "have": {z: sorted(v) for z, v in have.items()}}
    (BASE_DIR / "tiles.json").write_text(json.dumps(meta, separators=(",", ":")), encoding="utf-8", newline="\n")
    n = sum(len(v) for v in have.values())
    print(f"{n} 張大圖 × {len(THEMES)} 種配色，共 {total / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
