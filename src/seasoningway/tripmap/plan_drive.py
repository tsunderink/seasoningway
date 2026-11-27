"""逐天算「我的行程」的開車里程、時數和高速公路里程（OSRM，不含塞車、天候、休息、停留）。

用法：把 artifact 資料庫的 plan/itinerary（db_get 讀到的 JSON）存成檔案，再
  python3 -m seasoningway.tripmap.plan_drive trips/<旅程> 那個檔案.json
試算一天的另一種排法（第一個是出發點，最後一個是住處，中間是地點的 id；還沒加進 places/ 的地方寫「緯度,經度」）：
  python3 -m seasoningway.tripmap.plan_drive trips/<旅程> --route 出發點 景點 景點 住處
  python3 -m seasoningway.tripmap.plan_drive trips/<旅程> --route 出發點 景點 30.0200,140.0400
起點是前一晚的住處（沒定就是前一天最後一個點），終點是今晚住處；只算取車點（rental: pickup）到還車點（dropoff）之間。
到住處的時間：地圖設定 drive.day_start 出發（取車日從 pickup_time 起算）＋開車＋每個點的停留（地點的 visit 取中間值，
沒有的算 DEFAULT_VISIT 分鐘），晚於 drive.arrive_by 標出；開車超過 drive.max_hours 也標出。
地點 facts 寫了公休的週幾（「休息：週三、週四」「火曜定休」）或日期（YYYY-MM-DD）而那天剛好撞到，也標出（「每月第 1 個週二」、假日順延等不判斷）。
已決定的固定時段（例如某天下午要到某處）不管，那幾天自己對照旅程 README「已決定」。
結果只印出來，不存檔；要用的數字寫進 research/ 或 itinerary.json 時附查詢日期。
"""
import datetime
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from ..common import Problem, real_date
from .data import MapTrip

OSRM = "https://router.project-osrm.org/route/v1/driving/{}?overview=false&steps=true"
DEFAULT_VISIT = 60


def hours(text: str | None, default: float) -> float:
    """「9:00」→ 9.0"""
    if not text:
        return default
    h, m = text.split(":")
    return int(h) + int(m) / 60


def clock(h: float) -> str:
    """時數 → 「H:MM」，先四捨五入到分鐘再進位。

    >>> clock(9.5), clock(9 + 59.6 / 60), clock(23.999)
    ('9:30', '10:00', '24:00')
    """
    m = round(h * 60)
    return f"{m // 60}:{m % 60:02d}"


class Drive:
    """地圖設定 drive 的時間參數。"""

    def __init__(self, cfg: dict):
        d = cfg["drive"]
        self.max_h = d["max_hours"]
        self.start, self.pickup = hours(d.get("day_start"), 9.0), hours(d.get("pickup_time"), hours(d.get("day_start"), 9.0))
        self.arrive_by = hours(d.get("arrive_by"), 24.0)


def spots_of(t: MapTrip) -> dict:
    """id → {lat, lon, visit, facts, rental}（plan_drive 用到的欄位）"""
    return {p["id"]: {"id": p["id"], "lat": p["location"]["lat"], "lon": p["location"]["lon"], "visit": p.get("visit"),
                      "facts": [[f["label"], f["value"]] for f in p.get("facts", [])], "rental": p.get("rental")}
            for p in t.places}


def route(points: list[dict]) -> dict:
    coords = ";".join(f"{p['lon']},{p['lat']}" for p in points)
    for attempt in range(5):
        try:
            with urllib.request.urlopen(OSRM.format(coords), timeout=30) as response:
                return json.load(response)["routes"][0]
        except (OSError, ValueError, KeyError, IndexError) as error:
            if isinstance(error, urllib.error.HTTPError):
                error.close()
            if attempt < 4:
                time.sleep(3 * (attempt + 1))
    raise Problem("OSRM 沒有回傳可用的路線，稍後再試")


def expressway_km(r: dict) -> float:
    """步驟的 ref 是 E 開頭，或路名有「自動車道」「高速」的算高速公路（日本的寫法）。OSRM 常少走高速，當作下限。"""
    return sum(st["distance"] for leg in r["legs"] for st in leg["steps"]
               if re.search(r"\bE\d", st.get("ref") or "") or re.search("自動車道|高速", st.get("name") or "")) / 1000


def visit_min(spot: dict) -> float:
    v = spot.get("visit")
    return (v["min"] + v["max"]) / 2 if v else DEFAULT_VISIT


WEEKDAYS, WEEKDAYS_JA = "一二三四五六日", "月火水木金土日"


def closed(spot: dict, day: str) -> bool:
    """那天（YYYY-MM-DD）是不是公休。改規則後跑 python3 -m unittest discover -s core（tripmap 的測試會跑這裡的 doctest）。

    >>> f = lambda label, text: {"facts": [[label, text]]}
    >>> closed(f("休息（官網）", "週三、週四；看生長情況臨時休息"), "2099-01-08")  # 週四
    True
    >>> closed(f("休息（官網）", "週一（週一是假日就改休週二）"), "2099-01-06")  # 括號裡的週二不算
    False
    >>> closed(f("營業（じゃらん）", "9:00–16:30 火曜定休"), "2099-01-13")  # 週二
    True
    >>> closed(f("營業（じゃらん）", "毎週金曜日は20:00まで"), "2099-01-09")  # 週五；營業欄的「毎週」不是公休
    False
    >>> closed(f("休息（じゃらん）", "12月–3月 第4水曜日休館"), "2099-01-07")  # 第 1 個週三；「第幾個週幾」不判斷，也不當成每週
    False
    >>> closed(f("休館（官網）", "2098-12-03、2099-01-15"), "2099-01-15")
    True
    """
    d = datetime.date.fromisoformat(day)
    for label, text in spot.get("facts", []):
        rest = label.startswith("休")
        ja = {c for m in re.finditer(r"(?<!第\d)毎週([月火水木金土日])曜|(?<!第\d)([月火水木金土日](?:・[月火水木金土日])*)曜日?(?:定休|休)", text)
              if m.group(2) or rest for c in (m.group(1) or m.group(2)) if c != "・"}
        if WEEKDAYS_JA[d.weekday()] in ja:
            return True
        if rest:
            text = re.sub(r"（[^）]*）|每月第[^週]*週.", "", text)
            if day in text or WEEKDAYS[d.weekday()] in re.findall(r"週([一二三四五六日])", text):
                return True
    return False


def drive_report(trip_dir: Path, path: Path, today: datetime.date) -> str:
    days = json.loads(path.read_text(encoding="utf-8"))["days"]
    if isinstance(days, list):
        days = {day["date"]: {"ids": day.get("places", []), "stay": day.get("stay_place")} for day in days}
    if not isinstance(days, dict):
        raise Problem(f"{path}：days 要是行程紀錄或地圖頁的逐日資料")
    t = MapTrip(trip_dir)
    spots, drive = spots_of(t), Drive(t.config)
    for day, plan in days.items():
        if not real_date(day) or not isinstance(plan, dict) or not isinstance(plan.get("ids", []), list):
            raise Problem(f"{path}：{day} 的日期或行程格式錯誤")
        for point in [*plan.get("ids", []), plan.get("stay")]:
            if point is not None and (not isinstance(point, str) or point not in spots):
                raise Problem(f"{path}：{day} 指到不存在的地點 {point}")
    lines = []
    prev, total, total_ex, driving = None, 0.0, 0.0, False
    for day in sorted(days):
        ids, stay = days[day].get("ids", []), days[day].get("stay")
        pts = [p for p in [prev, *ids, stay] if p]
        pts = [p for i, p in enumerate(pts) if i == 0 or p != pts[i - 1]]
        # 只算有車的時候：取車點（pickup）之前、還車點（dropoff）之後不算
        pick = next((i for i, p in enumerate(pts) if spots[p]["rental"] == "pickup"), None)
        drop = next((i for i, p in enumerate(pts) if spots[p]["rental"] == "dropoff"), None)
        if pick is not None:
            pts, driving = pts[pick:], True
        if not driving:
            prev = stay or (ids[-1] if ids else prev)
            continue
        if drop is not None:
            pts, driving = pts[:drop - (pick or 0) + 1], False
        if len(pts) >= 2:
            r = route([spots[p] for p in pts])
            km, h, ex = r["distance"] / 1000, r["duration"] / 3600, expressway_km(r)
            total, total_ex = total + km, total_ex + ex
            flag = "  ⚠️ 超過每天開車上限" if h > drive.max_h else ""
            visit = sum(visit_min(spots[p]) for p in pts[1:-1] if spots[p]["rental"] != "pickup") / 60
            arrive = (drive.pickup if pick is not None else drive.start) + h + visit
            if stay and pts[-1] == stay:
                flag += f"  約 {clock(arrive)} 到住處" + (f"  ⚠️ 晚於 {clock(drive.arrive_by)}" if arrive > drive.arrive_by else "")
            shut = [p for p in pts[1:-1] if closed(spots[p], day)]
            if shut:
                flag += f"  ⚠️ 公休：{'、'.join(shut)}"
            wd = WEEKDAYS[datetime.date.fromisoformat(day).weekday()]
            lines.append(f"{day}（{wd}）{km:5.0f} km  {h:4.1f} 小時  高速 {ex:4.0f} km  {' → '.join(pts)}{'' if stay else '（沒排住處）'}{flag}")
            time.sleep(1)
        prev = stay or (ids[-1] if ids else prev)
    lines.append(f"合計 {total:.0f} km，高速至少 {total_ex:.0f} km（OSRM 路線，查詢於 {today.isoformat()}）")
    return "\n".join(lines) + "\n"


def main(trip_dir: str, path: str) -> int:
    print(drive_report(Path(trip_dir), Path(path), datetime.date.today()), end="")
    return 0


def try_route(trip_dir: str, tokens: list[str]) -> int:
    t = MapTrip(Path(trip_dir))
    spots, drive = spots_of(t), Drive(t.config)
    pts = []
    for tok in tokens:
        if m := re.fullmatch(r"(-?[\d.]+),(-?[\d.]+)", tok):
            pts.append({"id": tok, "lat": float(m[1]), "lon": float(m[2])})
        elif tok in spots:
            pts.append(spots[tok])
        else:
            sys.exit(f"places/ 沒有 {tok}.json")
    r = route(pts)
    h = r["duration"] / 3600
    arrive = drive.start + h + sum(visit_min(p) for p in pts[1:-1]) / 60
    print(f"{r['distance'] / 1000:.0f} km  {h:.1f} 小時  高速 {expressway_km(r):.0f} km  "
          f"{clock(drive.start)} 出發約 {clock(arrive)} 到終點（含中間各點停留）")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 4 and sys.argv[2] == "--route":
        sys.exit(try_route(sys.argv[1], sys.argv[3:]))
    if len(sys.argv) != 3 or sys.argv[2].startswith("--"):
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1], sys.argv[2]))
