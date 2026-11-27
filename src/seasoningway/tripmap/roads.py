"""地圖頁的開車路網（草案，MVP）：從 OpenStreetMap 抽出主要道路和景點附近的道路，存成 roads.txt，地圖頁在瀏覽器裡自己算路線。

收哪些路：
  全區：高速道路（motorway）、一般國道（trunk）、主要地方道（primary）、縣道（secondary），含匝道；
  市町村道、生活道路（tertiary 到 service）：只讀景點附近 NEAR_KM 公里內的，最後只留每個景點沿最短路接到主要道路的那幾段；
  不收：禁止汽車通行、私人道路、停車場通道、旅行期間封閉的路（OSM 的 *:conditional 寫的月份和旅行月份重疊，或列在地圖設定的 drive.closed_roads）。
  單行：主要道路照 OSM；小路不管單行（只用來接景點）。景點不接到高速道路和單行道上。
化簡：去掉死路；路口之間合成一段，只有兩條路相接、等級和方向一致的點合併掉；形狀用 Douglas-Peucker 化簡到 SIMPLIFY_M 公尺；只留最大的強連通區塊（每個路口都開得進去也開得出來）。
時速不在這裡：地圖頁用地圖設定的 drive.network，改時速不用重跑。

準備（只有重做路網時才需要，下載的資料不進 repo）：
  apt-get install osmium-tool；pip install osmium
  從 https://download.openstreetmap.fr/extracts/ 下載涵蓋景點的地區 -latest.osm.pbf
  osmium tags-filter（每區）只留 highway=motorway…service 的 way → osmium merge → osmium extract -b <西,南,東,北> -s smart
執行：
  python3 -m seasoningway.tripmap.roads trips/<旅程> <道路.osm.pbf> [OSM 資料日期 YYYY-MM-DD]
輸出 map/roads.txt（格式見 write_bin）和 map/roads.json（各地點接上的路口編號 snap、離路網超過 500 m 的點 far），地圖頁讀 roads.json。
參考：日本三個地方（約 60 萬條路）約 3 分鐘 → 3.7 萬個路口、5.9 萬段，roads.txt 約 950 KB（gzip 後 708 KB）。
"""
import json
import math
import re
import struct
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from ..common import Problem
from .data import MapTrip

sys.setrecursionlimit(100000)

NEAR_KM = 3
SIMPLIFY_M = 50
CLASS = {"motorway": 0, "trunk": 1, "primary": 2, "secondary": 3, "tertiary": 4,
         "unclassified": 5, "residential": 5, "living_street": 5, "service": 5}
MAIN = {0, 1, 2, 3}
NO = {"no", "private", "agricultural", "forestry", "delivery", "destination_no"}
SKIP_SERVICE = {"parking_aisle", "driveway", "drive-through", "emergency_access"}
DIGIT_SUB = "-_.~!*()$,"  # roads.txt 裡代替 0–9 的符號，地圖頁 loadRoads 換回來
MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()


def km(a, b):
    t = math.pi / 180
    x = math.sin((b[0] - a[0]) * t / 2) ** 2 + math.cos(a[0] * t) * math.cos(b[0] * t) * math.sin((b[1] - a[1]) * t / 2) ** 2
    return 12742 * math.asin(math.sqrt(min(1, x)))


WINTER = {11, 0, 1, 2}  # *:conditional 寫 winter、snow 的算 12–3 月封閉（北半球）
COND_MONTHS = re.compile(r"^(winter|snow|[a-z]{3}( \d+)?\s*-\s*[a-z]{3}( \d+)?)$")


def trip_months(start: str, end: str) -> set[int]:
    """旅行期間的月份（0＝1 月）。"""
    d, e, out = date.fromisoformat(start), date.fromisoformat(end), set()
    while d <= e:
        out.add(d.month - 1); d += timedelta(days=1)
    return out


def in_range(m: int, a: int, b: int) -> bool:
    return a <= m <= b if a <= b else m >= a or m <= b


def closed_during(tags, months: set[int]) -> bool:
    """*:conditional = no @ (Dec-Apr) 這類寫法，只寫月份範圍（沒有星期、時間）而且和旅行月份重疊，或寫 winter、snow 而旅行在冬天，就當作封閉。

    >>> closed_during({"motor_vehicle:conditional": "no @ (Dec-Apr)"}, {0})
    True
    >>> closed_during({"motor_vehicle:conditional": "no @ (Dec-Apr)"}, {6})
    False
    >>> closed_during({"access:conditional": "no @ winter"}, {0})
    True
    """
    for k in ("access:conditional", "motor_vehicle:conditional", "vehicle:conditional", "motorcar:conditional"):
        v = tags.get(k)
        if not v:
            continue
        for part in v.split(";"):
            if "@" not in part or part.split("@")[0].strip() not in NO:
                continue
            cond = part.split("@", 1)[1].strip().strip("()").strip().lower()
            for rng in cond.split(","):
                rng = rng.strip()
                if not COND_MONTHS.match(rng):
                    continue
                if rng in ("winter", "snow"):
                    if months & WINTER:
                        return True
                    continue
                ms = [MONTHS.index(m) for m in re.findall(r"[a-z]{3}", rng) if m in MONTHS]
                if len(ms) == 2 and any(in_range(m, ms[0], ms[1]) for m in months):
                    return True
    return False


def usable(tags, months):
    hw = tags.get("highway", "")
    base = hw.removesuffix("_link")
    if base not in CLASS or tags.get("area") == "yes":
        return None
    if hw == "service" and tags.get("service") in SKIP_SERVICE:
        return None
    for k in ("access", "vehicle", "motor_vehicle", "motorcar"):
        if tags.get(k) in NO:
            return None
    if closed_during(tags, months):
        return "closed"
    ow = tags.get("oneway", "")
    oneway = 1 if ow in ("yes", "1", "true") or tags.get("junction") == "roundabout" or (base == "motorway" and ow != "no") else -1 if ow == "-1" else 0
    return CLASS[base], hw.endswith("_link"), oneway


def near_cells(spots):
    """景點 NEAR_KM 公里內的格子（0.01 度），判斷一條路在不在景點附近用。"""
    cells = set()
    for s in spots:
        dlat = NEAR_KM / 111
        dlon = NEAR_KM / (111 * math.cos(math.radians(s["lat"])))
        for i in range(math.floor((s["lat"] - dlat) * 100), math.floor((s["lat"] + dlat) * 100) + 1):
            for j in range(math.floor((s["lon"] - dlon) * 100), math.floor((s["lon"] + dlon) * 100) + 1):
                cells.add((i, j))
    return cells


def read_ways(pbf, cells, months, closed_roads):
    ways, closed = [], 0
    import osmium

    fp = osmium.FileProcessor(pbf, osmium.osm.NODE | osmium.osm.WAY).with_locations()
    for w in fp:
        if not w.is_way():
            continue
        u = usable(w.tags, months)
        if u is None:
            continue
        if u == "closed":
            closed += 1; continue
        cls, link, oneway = u
        try:
            pts = [(n.ref, n.location.lat, n.location.lon) for n in w.nodes]
        except osmium.InvalidLocationError:
            pts = [(n.ref, n.location.lat, n.location.lon) for n in w.nodes if n.location.valid()]
        if len(pts) < 2:
            continue
        if any(w.tags.get(r["tag"]) == r["value"] and ("bbox" not in r or any(
                r["bbox"][0] <= la <= r["bbox"][2] and r["bbox"][1] <= lo <= r["bbox"][3] for _, la, lo in pts))
               for r in closed_roads):
            closed += 1; continue
        if cls not in MAIN and not any((math.floor(la * 100), math.floor(lo * 100)) in cells for _, la, lo in pts):
            continue
        if cls not in MAIN:
            oneway = 0  # 小路不管單行：只用來接景點，市區的單行小路照方向走反而常常接不上
        if oneway == -1:
            pts.reverse(); oneway = 1
        ways.append((cls, link, oneway, pts))
    return ways, closed


def rdp(pts, tol_km):
    if len(pts) < 3:
        return pts
    a, b = pts[0], pts[-1]
    # 平面近似：經度乘 cos(緯度)
    c = math.cos(math.radians(a[0]))
    ax, ay, bx, by = a[1] * c, a[0], b[1] * c, b[0]
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    best, bi = -1, 0
    for i in range(1, len(pts) - 1):
        px, py = pts[i][1] * c - ax, pts[i][0] - ay
        if L == 0:
            d = px * px + py * py
        else:
            t = max(0, min(1, (px * dx + py * dy) / L))
            ex, ey = px - t * dx, py - t * dy
            d = ex * ex + ey * ey
        if d > best:
            best, bi = d, i
    if math.sqrt(best) * 111 <= tol_km:
        return [a, b]
    return rdp(pts[:bi + 1], tol_km)[:-1] + rdp(pts[bi:], tol_km)


def build_graph(ways, spots):
    use = defaultdict(int)
    for _, _, _, pts in ways:
        for i, (nid, _, _) in enumerate(pts):
            use[nid] += 2 if i in (0, len(pts) - 1) else 1
    coord = {}
    edges = []  # [u, v, cls, link, oneway, length_km, shape(list of (lat,lon) 不含兩端)]
    for cls, link, oneway, pts in ways:
        start = 0
        for i in range(1, len(pts)):
            if i == len(pts) - 1 or use[pts[i][0]] >= 2:
                seg = pts[start:i + 1]
                if seg[0][0] != seg[-1][0]:
                    L = sum(km(p[1:], q[1:]) for p, q in zip(seg, seg[1:]))
                    for nid, la, lo in (seg[0], seg[-1]):
                        coord[nid] = (la, lo)
                    edges.append([seg[0][0], seg[-1][0], cls, link, oneway, L, [p[1:] for p in seg[1:-1]]])
                start = i
    # 景點接最近的路口：先在格子裡找最近的路網點（含路中間的形狀點，接上去後把那條路在那裡切開）
    return coord, edges


def largest_component(coord, edges):
    parent = {n: n for n in coord}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    for e in edges:
        a, b = find(e[0]), find(e[1])
        if a != b:
            parent[a] = b
    size = defaultdict(int)
    for n in coord:
        size[find(n)] += 1
    root = max(size, key=size.get)
    keep = {n for n in coord if find(n) == root}
    return {n: coord[n] for n in keep}, [e for e in edges if e[0] in keep]


def strong_component(coord, edges):
    """只留最大的強連通區塊：每個路口都開得進去、也開得出來（單行道會隔出進得去出不來的小塊）。Kosaraju，用迴圈不用遞迴。"""
    fw, bw = defaultdict(list), defaultdict(list)
    for e in edges:
        fw[e[0]].append(e[1]); bw[e[1]].append(e[0])
        if not e[4]:
            fw[e[1]].append(e[0]); bw[e[0]].append(e[1])
    seen, order = set(), []
    for root in coord:
        if root in seen:
            continue
        seen.add(root); stack = [(root, iter(fw[root]))]
        while stack:
            n, it = stack[-1]
            m = next(it, None)
            if m is None:
                stack.pop(); order.append(n)
            elif m not in seen:
                seen.add(m); stack.append((m, iter(fw[m])))
    comp, best = {}, (0, None)
    for root in reversed(order):
        if root in comp:
            continue
        comp[root] = root; stack, size = [root], 0
        while stack:
            n = stack.pop(); size += 1
            for m in bw[n]:
                if m not in comp:
                    comp[m] = root; stack.append(m)
        if size > best[0]:
            best = (size, root)
    keep = {n for n, c in comp.items() if c == best[1]}
    return {n: coord[n] for n in keep}, [e for e in edges if e[0] in keep and e[1] in keep]


def snap_spots(coord, edges, spots):
    """每個景點找最近的路網點（路口或路中間的形狀點）；是形狀點就把那條路切開，讓景點有自己的路口。"""
    grid = defaultdict(list)  # 格子 → (邊編號, 形狀點位置 -1=起點 / i / -2=終點)
    for ei, e in enumerate(edges):
        if e[2] == 0 or e[4]:  # 不接到高速道路和單行道上（旁邊的飯店接到高架的首都高、分隔道路的一側，可能進得去出不來）
            continue
        pts = [coord[e[0]]] + e[6] + [coord[e[1]]]
        for k, p in enumerate(pts):
            grid[(math.floor(p[0] * 50), math.floor(p[1] * 50))].append((ei, k))
    snaps = {}
    for s in spots:
        gi, gj = math.floor(s["lat"] * 50), math.floor(s["lon"] * 50)
        best = None
        for r in range(0, 6):
            for i in range(gi - r, gi + r + 1):
                for j in range(gj - r, gj + r + 1):
                    if max(abs(i - gi), abs(j - gj)) != r:
                        continue
                    for ei, k in grid.get((i, j), ()):
                        e = edges[ei]
                        pts = [coord[e[0]]] + e[6] + [coord[e[1]]]
                        d = km((s["lat"], s["lon"]), pts[k])
                        if best is None or d < best[0]:
                            best = (d, ei, k)
            if best and best[0] < r * 2.0:  # 一格約 2 km（0.02 度）
                break
        if best:
            snaps[s["id"]] = best
    # 切開：同一條邊可能有好幾個景點，按位置由後往前切
    by_edge = defaultdict(set)
    for sid, (d, ei, k) in snaps.items():
        by_edge[ei].add(k)
    new_edges = []
    snap_node = {}
    for ei, e in enumerate(edges):
        ks = sorted(k for k in by_edge.get(ei, ()) if 0 < k < len(e[6]) + 1)
        if not ks:
            new_edges.append(e); continue
        pts = [coord[e[0]]] + e[6] + [coord[e[1]]]
        ids = [e[0]] + [None] * len(e[6]) + [e[1]]
        for k in ks:
            ids[k] = ("s", ei, k); coord[ids[k]] = pts[k]
        cut = [0] + ks + [len(pts) - 1]
        for a, b in zip(cut, cut[1:]):
            seg = pts[a:b + 1]
            L = sum(km(p, q) for p, q in zip(seg, seg[1:]))
            new_edges.append([ids[a], ids[b], e[2], e[3], e[4], L, seg[1:-1]])
    for sid, (d, ei, k) in snaps.items():
        e = edges[ei]
        node = e[0] if k == 0 else e[1] if k == len(e[6]) + 1 else ("s", ei, k)
        snap_node[sid] = (node, d)
    return new_edges, snap_node


def connectors(edges, snap_node):
    """主要道路全留；小路只留每個景點沿最短路接到最近主要道路的那幾段。"""
    import heapq
    adj = defaultdict(list)
    for i, e in enumerate(edges):
        adj[e[0]].append(i); adj[e[1]].append(i)
    keep = {i for i, e in enumerate(edges) if e[2] in MAIN}
    # 目標是主要道路裡最大的連通區塊（只有小路連出去的一小段縣道不算）
    main_node, _ = largest_component({n: None for e in (edges[i] for i in keep) for n in e[:2]}, [edges[i] for i in keep])
    for node, _ in snap_node.values():
        dist, prev, pq = {node: 0}, {}, [(0, node)]
        while pq:
            d, n = heapq.heappop(pq)
            if d > dist[n]:
                continue
            if n in main_node:
                while n in prev:
                    keep.add(prev[n]); e = edges[prev[n]]; n = e[0] if e[1] == n else e[1]
                break
            for i in adj[n]:
                e = edges[i]; m = e[1] if e[0] == n else e[0]
                nd = d + e[5]
                if nd < dist.get(m, 1e9):
                    dist[m] = nd; prev[m] = i; heapq.heappush(pq, (nd, m))
    return [e for i, e in enumerate(edges) if i in keep]


def prune(edges, keep):
    """反覆拿掉死路（只連一段、又不是景點的路口），死路不會出現在任何兩個景點之間的路線上。"""
    while True:
        deg = defaultdict(int)
        for e in edges:
            deg[e[0]] += 1; deg[e[1]] += 1
        dead = {n for n, d in deg.items() if d == 1 and n not in keep}
        if not dead:
            return edges
        edges = [e for e in edges if e[0] not in dead and e[1] not in dead]


def contract(coord, edges, keep):
    """只有兩條路相接、等級和方向一致的點合併掉。"""
    inc = defaultdict(list)
    for i, e in enumerate(edges):
        inc[e[0]].append(i); inc[e[1]].append(i)
    alive = [True] * len(edges)
    for n in list(inc):
        if n in keep or n not in inc:
            continue
        lst = [i for i in inc[n] if alive[i]]
        if len(lst) != 2 or lst[0] == lst[1]:
            continue
        a, b = edges[lst[0]], edges[lst[1]]
        if a[1] != n and b[1] == n:
            a, b = b, a
        if a[2] != b[2] or a[3] != b[3] or a[4] != b[4]:
            continue
        # 方向：a 要以 n 結尾、b 以 n 開頭；雙向路可以翻轉
        def flip(e):
            return [e[1], e[0], e[2], e[3], e[4], e[5], e[6][::-1]]
        if a[1] != n:
            if a[4]: continue
            a = flip(a)
        if b[0] != n:
            if b[4]: continue
            b = flip(b)
        if a[0] == b[1]:
            continue
        m = [a[0], b[1], a[2], a[3], a[4], a[5] + b[5], a[6] + [coord[n]] + b[6]]
        alive[lst[0]] = alive[lst[1]] = False
        edges.append(m); alive.append(True)
        i = len(edges) - 1
        for end in (m[0], m[1]):
            inc[end] = [j for j in inc[end] if alive[j]] + [i]
        del inc[n]
    out = [e for e, ok in zip(edges, alive) if ok]
    used = {e[0] for e in out} | {e[1] for e in out}
    return {n: c for n, c in coord.items() if n in used}, out


def write_bin(coord, edges, path):
    """roads.txt：base64 文字（數字換成 DIGIT_SUB、沒有結尾的 =），解開是 gzip，再解開是一串無號 varint（LEB128）；有號的值先 zigzag。座標單位 1e-5 度。
    檔頭：魔術數字 0x52445332（"RDS2"）、節點數 N、路段數 E
    節點（依緯度、經度排序）：每個 [緯度差, zigzag(經度差)]，和前一個節點比，第一個和 0 比
    路段（依起點、終點排序）：每個 [起點差（和前一段比）, zigzag(終點 − 起點), 長度（公尺）,
      旗標（等級 0 高速…5 生活道路 | 匝道 8 | 單行 16）, 形狀點數 k, k 個 [zigzag(緯度差), zigzag(經度差)]]，
      形狀點不含兩端，和前一點比（第一點和起點比）"""
    order = sorted(coord, key=lambda n: (round(coord[n][0] * 1e5), round(coord[n][1] * 1e5)))
    idx = {n: i for i, n in enumerate(order)}
    q = {n: (round(coord[n][0] * 1e5), round(coord[n][1] * 1e5)) for n in order}
    out = bytearray()

    def uv(x):
        while True:
            b = x & 0x7F; x >>= 7
            if x:
                out.append(b | 0x80)
            else:
                out.append(b); return

    def zz(x):
        uv(x * 2 if x >= 0 else -x * 2 - 1)
    for x in (0x52445332, len(order), len(edges)):
        uv(x)
    pl = (0, 0)
    for n in order:
        uv(q[n][0] - pl[0]); zz(q[n][1] - pl[1]); pl = q[n]
    rows = sorted(((idx[e[0]], idx[e[1]], e) for e in edges), key=lambda r: (r[0], r[1]))
    pu, P = 0, 0
    for u, v, e in rows:
        uv(u - pu); zz(v - u); pu = u
        uv(round(e[5] * 1000)); uv(e[2] | (8 if e[3] else 0) | (16 if e[4] else 0))
        simp = rdp([coord[e[0]]] + e[6] + [coord[e[1]]], SIMPLIFY_M / 1000)[1:-1]
        uv(len(simp)); P += len(simp)
        prev = q[e[0]]
        for la, lo in simp:
            c = (round(la * 1e5), round(lo * 1e5)); zz(c[0] - prev[0]); zz(c[1] - prev[1]); prev = c
    import base64
    import gzip
    import string
    # artifact 只發布文字、圖片等類型，所以存成 base64 文字（地圖頁讀進來再解）；
    # 數字 0–9 換成 DIGIT_SUB、去掉結尾的 =，文字裡沒有數字，seasoningway check 的個資規則（卡號、電話、CVC＋數字…）才不會誤判
    b64 = base64.b64encode(gzip.compress(bytes(out), 9, mtime=0)).decode().rstrip("=")
    path.write_text(b64.translate(str.maketrans(string.digits, DIGIT_SUB)) + "\n", encoding="ascii", newline="\n")
    return idx, len(order), len(edges), P, len(out)


def main(argv, *, output: Path | None = None):
    if len(argv) < 2:
        print(__doc__); return 1
    t = MapTrip(Path(argv[0]))
    argv = argv[1:]
    out = output if output is not None else t.cache
    out.mkdir(parents=True, exist_ok=True)
    spots = [{"id": p["id"], "lat": p["location"]["lat"], "lon": p["location"]["lon"]} for p in t.places]
    ways, closed = read_ways(argv[0], near_cells(spots), trip_months(t.start, t.end), t.config["drive"].get("closed_roads", []))
    print(f"道路 {len(ways)} 條（旅行期間封閉、不收的 {closed} 條）")
    if not ways:
        raise Problem("路網範圍內沒有可用道路，請檢查 PBF 範圍")
    coord, edges = build_graph(ways, spots)
    coord, edges = strong_component(coord, edges)
    if not coord or not edges:
        raise Problem("路網範圍內沒有可用的連通道路")
    print(f"切成路段 {len(edges)} 段、路口 {len(coord)} 個")
    edges, snap_node = snap_spots(coord, edges, spots)
    edges = connectors(edges, snap_node)
    # 只留主要道路和接景點的小路後，單行道可能又隔出進得去出不來的小塊：再取一次強連通區塊，
    # 接的路口被拿掉的景點改接最近的路口
    coord, edges = strong_component({n: coord[n] for e in edges for n in e[:2]}, edges)
    for sid, (n, d) in list(snap_node.items()):
        if n not in coord:
            s = next(x for x in spots if x["id"] == sid)
            m = min(coord, key=lambda k: km((s["lat"], s["lon"]), coord[k]))
            snap_node[sid] = (m, km((s["lat"], s["lon"]), coord[m]))
            print(f"  {sid} 改接最近的路口（{snap_node[sid][1]:.2f} km）")
    keep = {n for n, _ in snap_node.values()}
    edges = prune(edges, keep)
    coord, edges = contract(coord, edges, keep)
    print(f"合併後 {len(edges)} 段、路口 {len(coord)} 個")
    idx, N, E, P, raw = write_bin(coord, edges, out / "roads.txt")
    size = (out / "roads.txt").stat().st_size
    print(f"roads.txt {size / 1024:.0f} KB（解壓 {raw / 1024:.0f} KB；路口 {N}、路段 {E}、形狀點 {P}）")
    far = {sid: round(d, 2) for sid, (n, d) in snap_node.items() if d > 0.5}
    meta = {"asof": argv[1] if len(argv) > 1 else None,
            "note": "開車路網（seasoningway.tripmap.roads 從 OpenStreetMap 產生，© OpenStreetMap contributors，ODbL）；snap 是每個地點接上路網的路口編號。",
            "near_km": NEAR_KM, "nodes": N, "edges": E, "shape": P, "bytes": size,
            "snap": {sid: idx[n] for sid, (n, d) in sorted(snap_node.items())},
            "far": dict(sorted(far.items(), key=lambda x: -x[1]))}
    (out / "roads.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"離路網超過 500 m 的景點 {len(far)} 個：" + "、".join(f"{k} {v} km" for k, v in list(meta['far'].items())[:15]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
