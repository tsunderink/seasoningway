"""驗證輸出、升級與四種快取指令。"""
import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import ModuleType
from unittest import mock
from urllib.error import HTTPError

from seasoningway import cli, config, records
from seasoningway.tripmap import place_ids, plan_drive, roads

TODAY = date(2098, 11, 17)
SLUG = "2099-01-example-city"
DEMO = Path(__file__).with_name("fixtures") / "demo"


class StageTwoTest(unittest.TestCase):
    def setUp(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.root = tmp / "repo"
        shutil.copytree(DEMO, self.root, ignore=shutil.ignore_patterns("out"))
        self.trip = self.root / "trips" / SLUG
        self.cache = self.trip / "cache"

    def run_cli(self, *args, expect=0):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(args), root=self.root, today=TODAY)
        output = out.getvalue() + err.getvalue()
        self.assertEqual(code, expect, output)
        return output

    def write(self, relative, text):
        target = self.trip / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
        return target

    def json(self, relative, data):
        return self.write(relative, json.dumps(data, ensure_ascii=False))

    def map_settings(self):
        self.json("map/config.json", {"$schema": records.schema_url("map"),
                  "title": "測試", "short_title": "測試", "storage_prefix": "test", "home": "old-town",
                  "categories": [{"id": "sight", "glyph": "景", "name": "景點"}, {"id": "car", "glyph": "車", "name": "租車"}],
                  "season": {"label": "冬", "tiers": {str(i): "範例" for i in range(1, 6)}},
                  "drive": {"max_hours": 4},
                  "basemap": {"wide": [7, 0, 1, 0, 1], "fine": [8, 0, 1, 0, 1]}})

    def to_v3(self):
        path = self.root / "seasoningway.toml"
        path.write_text(path.read_text(encoding="utf-8").replace(f"format = {config.FORMAT}", "format = 3"), encoding="utf-8")
        for source in records.record_paths(self.trip):
            data = json.loads(source.read_text(encoding="utf-8"))
            data.pop("$schema")
            source.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_render_validates_before_writing_and_preserves_previous_output(self):
        self.run_cli("render")
        path = self.root / "out" / SLUG / "itinerary.md"
        rendered = path.read_bytes()
        self.assertIn(f"../../trips/{SLUG}/research/lodging.md", rendered.decode("utf-8"))
        self.assertIn("../profile/preferences.md", (self.root / "out/index.md").read_text(encoding="utf-8"))
        self.write("budget.json", "{")
        self.assertIn("JSON 格式錯誤", self.run_cli("render", SLUG, expect=1))
        self.assertEqual(path.read_bytes(), rendered)
        self.assertFalse((self.trip / "itinerary.md").exists())

    def test_migrate_v3_adds_schema_and_moves_caches(self):
        self.to_v3()
        budget = self.write("budget.json", '{"rates":[],"items":[],"test_number": 0.1234567890123456789}')
        self.write("map/roads.txt", "roads")
        self.json("map/roads.json", {"snap": {}})
        self.json("map/base/tiles.json", {"ranges": {}})
        self.json("map/place_ids.json", {"ids": {}})
        self.json("map/published.json", {"slug": "example", "version": "old", "include": ["index.html"], "files": {}})
        self.write("map/index.html", '<html>const BASE = {}; const DATA = {}; <div id="view-days"></div></html>')
        self.write("bookings.md", "<!-- 由 seasoningway 產生，請勿手動編輯。 -->\n")
        custom = self.write("budget.md", "# 手寫筆記\n")
        self.write("extra.md", "[預訂](bookings.md)、[bookings.md](bookings.md)\n")
        self.run_cli("migrate")
        self.assertEqual(config.load_config(self.root).format, 4)
        for path in records.record_paths(self.trip):
            kind = records.record_kind(path.relative_to(self.root).as_posix())
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["$schema"], records.schema_url(kind))
        self.assertIn("0.1234567890123456789", budget.read_text(encoding="utf-8"))
        for name in ("roads.txt", "roads.json", "base/tiles.json", "place_ids.json"):
            self.assertTrue((self.cache / name).is_file(), name)
            self.assertFalse((self.trip / "map" / name).exists(), name)
        state = json.loads((self.trip / "map/published.json").read_text(encoding="utf-8"))
        self.assertEqual(state, {"$schema": records.schema_url("published"), "slug": "example", "version": "old", "inputs": {}})
        self.assertFalse((self.trip / "map/index.html").exists())
        self.assertFalse((self.trip / "bookings.md").exists())
        self.assertTrue(custom.is_file())
        self.assertEqual((self.trip / "extra.md").read_text(encoding="utf-8"), "[預訂](bookings.json)、[bookings.json](bookings.json)\n")
        self.assertFalse((self.root / "out").exists())
        self.run_cli("migrate")

    def test_migrate_conflicting_cache_leaves_format_and_records_intact(self):
        self.to_v3()
        old = self.write("map/roads.txt", "v3")
        existing = self.write("cache/roads.txt", "v4")
        self.assertIn("整合兩份快取", self.run_cli("migrate", expect=1))
        self.assertEqual(old.read_text(encoding="utf-8"), "v3")
        self.assertEqual(existing.read_text(encoding="utf-8"), "v4")
        self.assertEqual(config.load_config(self.root).format, 3)
        self.assertNotIn("$schema", (self.trip / "budget.json").read_text(encoding="utf-8"))

    def test_fetch_drive_reports_and_keeps_previous_on_failure(self):
        self.map_settings()
        pickup = json.loads((self.trip / "places/old-town.json").read_text(encoding="utf-8"))
        pickup.update({"rental": "pickup", "categories": ["car"]})
        self.json("places/old-town.json", pickup)
        self.json("itinerary.json", {"$schema": records.schema_url("itinerary"), "days": [
            {"date": "2099-01-15", "places": ["old-town", "riverside-path"]}]})
        with mock.patch.object(plan_drive, "route", return_value={"distance": 32000, "duration": 3600, "legs": []}) as route, \
                mock.patch.object(plan_drive.time, "sleep"):
            output = self.run_cli("fetch", SLUG, "drive")
        route.assert_called_once()
        path = self.cache / "drive.txt"
        original = path.read_bytes()
        self.assertIn("合計 32 km", output)
        self.assertIn(TODAY.isoformat(), original.decode("utf-8"))
        with mock.patch.object(plan_drive, "route", side_effect=OSError("offline")):
            self.assertIn("offline", self.run_cli("fetch", SLUG, "drive", expect=1))
        self.assertEqual(path.read_bytes(), original)

    def test_fetch_place_ids_preserves_cache_after_partial_failure(self):
        self.map_settings()
        cached = self.json("cache/place_ids.json", {"ids": {"old-town": "example-existing"}})
        initial = cached.read_bytes()
        found = [{"id": "example-candidate", "location": {"latitude": 30.02, "longitude": 140.04},
                  "displayName": {"text": "範例景點"}, "formattedAddress": "範例地點"}]
        error = HTTPError("https://example.invalid", 429, "quota", None, None)
        with mock.patch.object(place_ids, "search", side_effect=[found, error]), mock.patch.object(place_ids.time, "sleep"):
            self.assertIn("429", self.run_cli("fetch", SLUG, "place-ids", "--all", expect=1))
        self.assertEqual(cached.read_bytes(), initial)
        with mock.patch.object(place_ids, "search", return_value=found), mock.patch.object(place_ids.time, "sleep"):
            self.run_cli("fetch", SLUG, "place-ids", "--ids", "example-temple")
        state = json.loads(cached.read_bytes())
        self.assertEqual(state["ids"], {"old-town": "example-existing", "example-temple": "example-candidate"})
        self.assertNotIn("formattedAddress", state)
        self.assertIn("找不到地點", self.run_cli("fetch", SLUG, "place-ids", "--ids", "unknown", expect=1))

    def test_fetch_roads_installs_both_files_and_rejects_partial_results(self):
        self.map_settings()
        pbf = self.write("test.osm.pbf", "fixture placeholder")

        def partial(argv, *, output):
            (output / "roads.txt").write_text("test roads", encoding="utf-8")
            return 0

        with mock.patch.object(roads, "main", side_effect=partial):
            self.assertIn("沒有產生 roads.json", self.run_cli("fetch", SLUG, "roads", "--pbf", str(pbf), expect=1))
        self.assertFalse((self.cache / "roads.txt").exists())

        def produce(argv, *, output):
            partial(argv, output=output)
            (output / "roads.json").write_text('{"snap":{}}', encoding="utf-8")
            self.assertEqual(argv[-1], "2098-11-16")
            return 0

        with mock.patch.object(roads, "main", side_effect=produce):
            self.run_cli("fetch", SLUG, "roads", "--pbf", str(pbf), "--date", "2098-11-16")
        self.assertTrue((self.cache / "roads.txt").is_file())
        self.assertTrue((self.cache / "roads.json").is_file())
        with mock.patch.object(roads, "main", side_effect=ModuleNotFoundError(name="osmium")):
            self.assertIn("需要 osmium", self.run_cli("fetch", SLUG, "roads", "--pbf", str(pbf), expect=1))

    def test_fetch_basemap_replaces_cache_only_after_complete_refresh(self):
        self.map_settings()
        rail = self.json("rail.json", {"features": []})
        work = self.root.parent / "work"
        (work / "node_modules/maplibre-gl").mkdir(parents=True)
        (work / "node_modules/@protomaps/basemaps").mkdir(parents=True)
        before = self.write("cache/base/tiles.json", "old tiles")
        fake = ModuleType("seasoningway.tripmap.basemap_tiles")

        def produce(argv, *, output):
            self.assertEqual(argv[-1], "20981116")
            output.mkdir()
            (output / "tiles.json").write_text("new tiles", encoding="utf-8")
            return 0

        fake.main = produce
        args = ("fetch", SLUG, "basemap", "--rail", str(rail), "--work", str(work), "--date", "20981116")
        with mock.patch.dict(sys.modules, {fake.__name__: fake}):
            self.assertIn("缺少 tiles.json 或 places.json", self.run_cli(*args, expect=1))
            self.assertEqual(before.read_text(encoding="utf-8"), "old tiles")

            def complete(argv, *, output):
                produce(argv, output=output)
                (output / "places.json").write_text("[]", encoding="utf-8")
                return 0

            fake.main = complete
            self.run_cli(*args)
        self.assertEqual(before.read_text(encoding="utf-8"), "new tiles")
        self.assertTrue((self.cache / "base/places.json").is_file())


if __name__ == "__main__":
    unittest.main()
