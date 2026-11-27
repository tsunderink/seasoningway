"""查每個點在 Google 地圖上的 Place ID，寫進 place_ids.json；「在 Google 地圖看這天路線」的連結帶上它，Google 才一定開到那個地點。

用 Places API (New) 的 Text Search：搜尋文字是原文名稱＋地區（data.py 的 map_query，地點的 map_query 可以指定），
語言和國家是地圖設定的 search.language、search.region_code；以我們的座標為中心偏向搜尋：
Google 排第一的結果在 2 km 內就用它，否則取離我們座標最近的。map_query 是 false 的點（泛指一類的店、列車）不查。

金鑰：環境變數 GOOG_MAPS_DEMO_KEY；沒有的話不帶金鑰，靠環境的網路密鑰（places.googleapis.com 的 X-Goog-Api-Key）由代理補上。
金鑰不寫進任何檔案。Google 的名稱、座標、地址只印出來對照，不存（Google Maps Platform 只允許長期保存 Place ID）。

python3 -m seasoningway.tripmap.place_ids trips/<旅程> id1 id2    # 只查（或重查）這幾個點：新排進「我的行程」的點用這個
python3 -m seasoningway.tripmap.place_ids trips/<旅程> --missing  # 查所有還沒有 Place ID 的點（點多時 demo key 的額度不夠，不要用）
python3 -m seasoningway.tripmap.place_ids trips/<旅程> --all      # 全部重查（同上，不要用）

結果寫進 map/place_ids.json。離我們的座標 2 km 以內的直接採用；2–10 km 印出來，加 --accept id1,id2 才採用；
10 km 以上不採用（改地點的 map_query）。寫完執行 seasoningway build。
"""
import datetime
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from .data import MapTrip, map_query

URL = "https://places.googleapis.com/v1/places:searchText"
FIELDS = "places.id,places.displayName,places.location,places.formattedAddress"
NEAR_KM, FAR_KM = 2, 10


def km(a: tuple, b: tuple) -> float:
    t = math.pi / 180
    x = math.sin((b[0] - a[0]) * t / 2) ** 2 + math.cos(a[0] * t) * math.cos(b[0] * t) * math.sin((b[1] - a[1]) * t / 2) ** 2
    return 12742 * math.asin(math.sqrt(x))


def search(q: str, lat: float, lon: float, opts: dict) -> list[dict]:
    body = {"textQuery": q, "pageSize": 5, **({"languageCode": opts["language"]} if "language" in opts else {}),
            **({"regionCode": opts["region_code"]} if "region_code" in opts else {}),
            "locationBias": {"circle": {"center": {"latitude": lat, "longitude": lon}, "radius": 5000.0}}}
    headers = {"Content-Type": "application/json", "X-Goog-FieldMask": FIELDS}
    key = os.environ.get("GOOG_MAPS_DEMO_KEY")
    if key:
        headers["X-Goog-Api-Key"] = key
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r).get("places", [])


def main(argv: list[str]) -> int:
    accept = set()
    for a in [a for a in argv if a.startswith("--accept")]:
        accept |= set(a.split("=", 1)[1].split(",")) if "=" in a else set()
    args = [a for a in argv if not a.startswith("--")]
    if not args:
        print(__doc__); return 1
    t = MapTrip(Path(args.pop(0)))
    OUT = t.map / "place_ids.json"
    opts = t.config.get("search", {})
    spots = [{"id": p["id"], "lat": p["location"]["lat"], "lon": p["location"]["lon"], "gq": map_query(p, t.config)} for p in t.places]
    saved = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {"ids": {}}
    ids = saved["ids"]
    if not args and "--all" not in argv and "--missing" not in argv:
        print(__doc__); return 1
    todo = [s for s in spots if s.get("gq") and (s["id"] in args if args else ("--all" in argv or s["id"] not in ids))]
    print(f"要查 {len(todo)} 個點")
    review, miss, done = [], [], 0
    for s in todo:
        try:
            found = search(s["gq"], s["lat"], s["lon"], opts)
        except urllib.error.HTTPError as e:
            print(f"Places API 回應 {e.code}：{e.read().decode(errors='replace')[:300]}")
            break
        time.sleep(0.15); done += 1
        dist = lambda p: km((s["lat"], s["lon"]), (p["location"]["latitude"], p["location"]["longitude"]))
        # Google 排第一的在 2 km 內就用它（最近的常是旁邊的旅館、館內設施）；否則取最近的
        best = found[0] if found and dist(found[0]) <= NEAR_KM else min(found, key=dist, default=None)
        if not best:
            miss.append(s["id"]); print(f"✗ {s['id']}：「{s['gq']}」找不到"); continue
        d = km((s["lat"], s["lon"]), (best["location"]["latitude"], best["location"]["longitude"]))
        line = f"{s['id']}：「{s['gq']}」→ {best['displayName']['text']}（{best.get('formattedAddress', '')}），差 {d:.1f} km"
        if d <= NEAR_KM or (d <= FAR_KM and s["id"] in accept):
            ids[s["id"]] = best["id"]; print("✓ " + line)
        elif d <= FAR_KM:
            review.append(s["id"]); print("？ " + line)
        else:
            miss.append(s["id"]); ids.pop(s["id"], None); print("✗ " + line)
    if not done:
        return 1
    saved = {"asof": datetime.date.today().isoformat(),
             "note": "Google 地圖的 Place ID（place_ids.py 用 Places API Text Search 查，和我們的座標對照過）；地圖頁的 Google 地圖路線連結用它指定地點。",
             "ids": dict(sorted(ids.items()))}
    OUT.write_text(json.dumps(saved, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"有 Place ID 的點 {len(ids)} 個；待確認 {len(review)} 個：{','.join(review)}；沒採用 {len(miss)} 個：{','.join(miss)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
