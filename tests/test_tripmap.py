"""地圖資料、模板與瀏覽器測試。"""
import contextlib
import doctest
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from datetime import date
from importlib.resources import as_file, files
from pathlib import Path

from fixture_project import seed_project
from seasoningway import checks, cli, common, records
from seasoningway.tripmap import plan_drive, render, roads
from seasoningway.tripmap.data import MapTrip, map_query, page_data, visit_text

CONFIG = {
    "title": "測試地圖 /*__DATA__*/null", "short_title": "測試", "storage_prefix": "test", "home": "station",
    "categories": [{"id": "sight", "glyph": "景", "name": "景點"}, {"id": "base", "glyph": "住", "name": "住宿"}],
    "season": {"label": "1 月", "tiers": {str(i): f"第 {i} 級" for i in range(1, 6)}},
    "ratings": [{"site": "b-site", "name": "B"}, {"site": "a-site", "name": "A", "missing": "沒有 A 的分數"}],
    "search": {"region_replace": [["縣", "県"]]},
    "drive": {"max_hours": 4},
    "basemap": {"wide": [7, 0, 1, 0, 1], "fine": [8, 0, 1, 0, 1],
                "credits": [{"what": "鐵路", "source": "測試<資料>", "url": "https://example.com/rail"}]},
    "preset": {"2099-01-02": ["temple"]},
}


INJECTED = "摘要 A&B </script><script>document.title='INJECTED'</script> /*__BASE__*/null"  # 抄自網頁的文字不能跳出 <script>、不能被當成佔位符


def place(pid: str, **extra) -> dict:
    p = {"id": pid, "name": f"{pid} 名稱", "region": "測試縣", "location": {"lat": 35.0, "lon": 135.0},
         "categories": ["sight"], "summary": "摘要", "season": {"tier": 3, "why": "理由"},
         "sources": [{"url": "https://example.com/" + pid, "title": "來源"}, {"url": "research/a.md"}], "as_of": "2099-01-10"}
    return {**p, **extra}


class MapTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.slug = "2099-01-test"
        self.trip = self.root / "trips" / self.slug
        self.today = date(2098, 11, 1)
        seed_project(self.root)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["new", self.slug, "--currency", "TWD"], root=self.root, today=self.today), 0)
        (self.trip / "places").mkdir()
        (self.trip / "map").mkdir()
        (self.trip / "cache/base").mkdir(parents=True)
        (self.trip / "research" / "a.md").write_text("# a\n", encoding="utf-8")
        (self.trip / "README.md").write_text("---\ntitle: 測試\nstatus: 規劃中\nstart: 2099-01-01\nend: 2099-02-02\n"
                                             "travelers: 2\ncurrency: TWD\n---\n# 測試\n", encoding="utf-8")
        self.write("map/config.json", CONFIG)
        self.write("places/station.json", place("station", categories=["base"], season={}, local_name="駅",
                                                contact={"address": "某處 1-1", "phone": "+81-3-0000-0000"}))
        self.write("places/temple.json", place("temple", local_name="寺【本堂】（東）", summary=INJECTED,
                                               sources=[{"url": "https://example.com/temple", "title": "來源"}, {"url": "research/a.md"},
                                                        {"url": "https://www.example.org/untitled"}], ratings=[
            {"site": "a-site", "score": 4.1, "scale": 5, "url": "https://example.com/a"},
            {"site": "b-site", "score": "3.5–3.6", "scale": 5, "url": "https://example.com/b"},
            {"site": "other", "score": 1, "scale": 5, "url": "https://example.com/o"}],
            visit={"min": 60, "max": 90, "note": "依據", "sources": [], "as_of": "2099-01-10"}))
        self.write("cache/place_ids.json", {"asof": "2099-01-10", "note": "測試", "ids": {"temple": "ChIJ-test"}})

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel: str, data) -> None:
        kind = records.record_kind(f"trips/{self.slug}/{rel}")
        payload = {"$schema": records.schema_url(kind), **data} if kind else data
        (self.trip / rel).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def test_page_data_maps_place_records_to_page_fields(self):
        data = page_data(MapTrip(self.trip))
        spots = {s["id"]: s for s in data["spots"]}
        temple, station = spots["temple"], spots["station"]
        self.assertEqual((temple["local"], temple["region"], temple["tier"], temple["tier_why"]), ("寺【本堂】（東）", "測試縣", 3, "理由"))
        self.assertEqual([r["site"] for r in temple["ratings"]], ["b-site", "a-site"])  # 照設定排、設定沒有的網站不放
        self.assertEqual(temple["sources"], [{"t": "來源", "u": "https://example.com/temple"},  # 研究筆記的相對路徑不放
                                             {"t": "www.example.org", "u": "https://www.example.org/untitled"}])  # 沒標題用網域
        self.assertEqual((temple["gq"], temple["gpid"]), ("寺 東 測試県", "ChIJ-test"))
        self.assertEqual(temple["visit"]["min"], 60)
        self.assertEqual(station["facts"], [["地址", "某處 1-1"], ["電話", "+81-3-0000-0000"]])
        self.assertNotIn("tier", station)
        self.assertEqual(data["trip"], {"start": "2099-01-01", "end": "2099-02-02", "home": "station", "holidays": {},
                                        "notes": {}, "preset": {"2099-01-02": ["temple"]}})
        self.assertEqual(data["page"]["view"], {"lat": 35.0, "lon": 135.0, "zoom": 6})
        self.assertEqual(data["page"]["texts"]["region_word"], "地區")
        self.assertEqual(data["cats"], [["sight", "景", "景點"], ["base", "住", "住宿"]])

    def test_bad_records_raise_the_same_messages_as_check(self):
        self.write("places/temple.json", place("shrine"))
        with self.assertRaises(common.Problem) as e:
            MapTrip(self.trip)
        self.assertIn("places/temple.json：id shrine 要和檔名（temple）一樣", str(e.exception))
        self.write("places/temple.json", place("temple", categories=["food"]))
        with self.assertRaises(common.Problem) as e:
            page_data(MapTrip(self.trip))
        self.assertIn("categories 沒有定義：food", str(e.exception))
        self.write("places/temple.json", place("temple"))
        self.write("map/config.json", {**CONFIG, "home": "airport"})
        with self.assertRaises(common.Problem) as e:
            page_data(MapTrip(self.trip))
        self.assertIn("不存在的地點 airport", str(e.exception))

    def test_map_query(self):
        cfg = {"search": {"region_replace": [["縣", "県"]]}}
        base = {"name": "甲", "region": "甲縣・乙縣"}
        self.assertEqual(map_query({**base, "local_name": "【春】乙（丙）"}, cfg), "乙 丙 甲県")
        self.assertEqual(map_query({**base, "map_query": "指定"}, cfg), "指定")
        self.assertIsNone(map_query({**base, "map_query": False}, cfg))
        self.assertEqual([visit_text({"min": a, "max": b}) for a, b in ((30, 45), (60, 90), (45, 60), (75, 75))],
                         ["30–45 分鐘", "1–1.5 小時", "45 分鐘–1 小時", "1 小時 15 分鐘"])

    def write_map_inputs(self) -> None:
        """build 需要、但 page_data 用不到的地圖輸入：縣界、底圖索引、底圖地名。"""
        ring = [[134.9, 34.9], [135.1, 34.9], [135.1, 35.1], [134.9, 35.1], [134.9, 34.9]]
        self.write("map/regions.json", {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"zh": "測試縣", "focus": True}, "geometry": {"type": "MultiPolygon", "coordinates": [[ring]]}}]})
        self.write("cache/base/tiles.json", {"date": "20990101", "fine_min": 8, "atlas": 4, "have": {},
                                           "ranges": {"7": [0, 1, 0, 1], "8": [0, 1, 0, 1]}})
        self.write("cache/base/places.json", [])

    def test_render_explains_missing_map_inputs(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["render", self.slug], root=self.root, today=self.today), 1)
        self.assertIn("還缺 map/regions.json、cache/base/tiles.json、cache/base/places.json", out.getvalue())
        self.assertFalse((self.root / "out").exists())

    def test_check_reports_malformed_basemap_cache(self):
        self.write_map_inputs()
        self.write("cache/base/tiles.json", {"fine_min": 8, "ranges": {}})
        problems, _ = checks.run_check(self.root, self.today)
        self.assertIn("cache/base/tiles.json 的縮放範圍無效", "\n".join(problems))

    def test_page_reads_cache_and_renders(self):
        self.write_map_inputs()
        images = self.trip / "cache/base/tiles/light/8"
        images.mkdir(parents=True)
        (images / "0-0.webp").write_bytes(b"test tiles")
        self.write("cache/roads.json", {"asof": "2099-01-01", "snap": {}})
        (self.trip / "cache/roads.txt").write_text("test road network", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["render", self.slug], root=self.root, today=self.today), 0)
        out = self.root / "out" / self.slug / "map"
        self.assertEqual((out / "roads.txt").read_text(encoding="utf-8"), "test road network")
        self.assertEqual((out / "base/tiles/light/8/0-0.webp").read_bytes(), b"test tiles")
        self.assertFalse((self.trip / "map/index.html").exists())
        page = (out / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("<!--__", page)
        self.assertEqual(page.count("/*__"), 2)  # 佔位符都換掉了，只剩標題和地點摘要裡故意寫的
        self.assertIn("<title>測試地圖 /*__DATA__*/null</title>", page)  # 先填進去的文字不會再被換掉
        self.assertIn("2099 年 1 月 1 日至 2 月 2 日，共 33 天。", page)
        self.assertIn('<a href="https://example.com/rail">測試&lt;資料&gt;</a>', page)
        template = files("seasoningway.tripmap").joinpath("template.html").read_text(encoding="utf-8")
        self.assertEqual(page.count("</script>"), template.count("</script>"))  # 地點的文字沒有跳出 script
        base = json.loads(re.search(r"const BASE = (\{.*?\});", page).group(1))
        for key in ("fine", "wide"):  # 底圖範圍四捨五入過：Windows 和 Linux 的數學函式庫最後一位可能不同
            for v in base[key].values():
                self.assertEqual(v, round(v, 6))

    def test_page_runs_in_browser(self):
        """模板的 JS 只有這裡測：桌面、手機尺寸各開一次，切三個分頁、收合地圖（只有手機有），不能有 JS 錯誤。
        要 Node 的 playwright 和 leaflet 套件。CI 設 SEASONINGWAY_BROWSER_TEST=require，缺了就算失敗。"""
        self.write_map_inputs()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["render", self.slug], root=self.root, today=self.today), 0)
        result = browse(self.root / "out" / self.slug / "map/index.html")
        if "skip" in result:
            if os.environ.get("SEASONINGWAY_BROWSER_TEST") == "require":
                self.fail("瀏覽器測試跑不起來：" + result["skip"])
            self.skipTest(result["skip"])
        self.assertNotIn("error", result)
        for size, r in result.items():
            with self.subTest(size):
                self.assertEqual(r["errors"], [])
                self.assertNotIn("INJECTED", r["title"])
                self.assertIn("住宿・旅館（起＝station 名稱）", r["legend"])  # config.home
                days, spots, hotels = r["views"]["view-days"], r["views"]["view-list"], r["views"]["view-hotels"]
                self.assertIn("・33 天", days)
                self.assertIn("第 2 天", days)
                self.assertIn("temple 名稱", days)  # config.preset 排在 1/2
                self.assertIn("第 2 天・1/2（五）", spots)
                self.assertIn("已定住宿 0 / 32 晚", hotels)
                self.assertEqual(r["maptoggle"], size == "phone")

    def test_dates_text(self):
        self.assertEqual(render.dates_text("2099-01-03", "2099-01-08"), "2099 年 1 月 3 日至 8 日，共 6 天。")
        self.assertEqual(render.dates_text("2098-12-30", "2099-01-02"), "2098 年 12 月 30 日至 2099 年 1 月 2 日，共 4 天。")


def browse(page: Path) -> dict:
    """用 test_page.js 在瀏覽器打開地圖頁，回傳它印的 JSON。沒有 node 時回傳 {"skip": 原因}。"""
    if not shutil.which("node"):
        return {"skip": "沒有 node"}
    with as_file(files("seasoningway.tripmap").joinpath("test_page.js")) as script:
        run = subprocess.run(["node", str(script), str(page)],
                             capture_output=True, encoding="utf-8", timeout=180)
    lines = run.stdout.strip().splitlines()
    if not lines:
        return {"error": run.stderr.strip()[-2000:] or f"node 結束碼 {run.returncode}"}
    return json.loads(lines[-1])


def load_tests(loader, tests, pattern):
    """加上駕車試算和路網的 doctest。"""
    for module in (plan_drive, roads):
        tests.addTests(doctest.DocTestSuite(module))
    return tests
