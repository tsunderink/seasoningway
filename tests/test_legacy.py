"""以虛構舊模板測試遷移，並比對舊 build 的實際產物。"""
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tomllib
from datetime import date
from pathlib import Path
from unittest.mock import patch

from seasoningway import __version__, cli
from seasoningway.config import FORMAT
from seasoningway.records import schema_url
from test_adapters import DataRepoTest

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXTURE = FIXTURES / "legacy-template"
SLUG = "2099-01-example-city"
TRIP = Path("trips") / SLUG
TODAY = date(2098, 11, 17)
VIEWS = ("budget.md", "bookings.md", "itinerary.md")
OLD_MAP = '<html><script>const BASE = {}; const DATA = {};</script><div id="view-days"></div></html>\n'


def expected_view(old: str, name: str) -> str:
    """保留舊內容，只替換已知的產生提示和搬移後的連結。"""
    header, rest = old.split("\n", 1)
    prefix = "<!-- 由 core/trips.py 產生，請勿手動編輯。"
    assert header.startswith(prefix)
    commands = {
        "index.md": "執行 seasoningway render 更新",
        "budget.md": f"修改 budget.json 後執行 seasoningway render {SLUG}",
        "bookings.md": "修改 bookings.json 後執行 seasoningway render",
        "itinerary.md": "修改 itinerary.json 後執行 seasoningway render",
    }
    if name == "index.md":
        for relative in (f"trips/{SLUG}/README.md", "profile/preferences.md", "profile/lessons.md", "wishlist.md"):
            rest = rest.replace(f"]({relative})", f"](../{relative})")
    elif name == "itinerary.md":
        rest = rest.replace("](research/lodging.md)", f"](../../trips/{SLUG}/research/lodging.md)")
    return f"<!-- 由 seasoningway 產生，請勿手動編輯。{commands[name]} -->\n{rest}"


class LegacyMigrationTest(DataRepoTest):
    def setUp(self):
        super().setUp()
        shutil.copytree(FIXTURE, self.root, dirs_exist_ok=True)
        if core := os.environ.get("SEASONINGWAY_LEGACY_CORE"):
            self.env["PYTHONDONTWRITEBYTECODE"] = "1"
            self.ok(self.process([sys.executable, str(Path(core).resolve() / "trips.py"),
                                  "--root", str(self.root), "build"]))
        else:
            self.write(f"data/{TRIP.as_posix()}/map/index.html", OLD_MAP)
        self.ok(self.git("init", "--initial-branch=main"))
        self.ok(self.git("config", "--local", "core.hooksPath", "core/githooks"))
        self.ok(self.git("config", "--local", "core.autocrlf", "false"))
        self.save_legacy()

    def save_legacy(self):
        self.ok(self.git("add", "--all"))
        self.ok(self.git("commit", "-m", "虛構舊模板"))

    def test_migrates_renders_and_saves_in_one_checkout(self):
        self.write("data/extra.md", "# 已追蹤的額外資料\n")
        self.write("notes.example", "保留 repo 其他已追蹤檔案\n")
        self.write(".github/workflows/custom.yml", "# 使用者自己的 CI\n")
        self.write(".claude/settings.json", '{"hooks": {}}\n')
        self.write(".codex/agents/old.toml", '# 舊角色\n')
        self.save_legacy()
        original = self.ok(self.git("rev-parse", "HEAD")).strip()
        old_trip = self.root / "data" / TRIP
        old = {name: (old_trip / name).read_text(encoding="utf-8") for name in VIEWS}
        old["index.md"] = (self.root / "data/README.md").read_text(encoding="utf-8")
        if os.environ.get("SEASONINGWAY_LEGACY_CORE"):
            digest = hashlib.sha256((old_trip / "map/index.html").read_bytes()).hexdigest()
            self.assertEqual(digest, (FIXTURES / "legacy-template-map.sha256").read_text(encoding="ascii").strip())
        cache_names = ("roads.txt", "roads.json", "place_ids.json", "base/places.json", "base/tiles.json")
        caches = {name: (old_trip / "map" / name).read_bytes() for name in cache_names}
        profile = (self.root / "data/profile/preferences.md").read_bytes()

        nested = self.cli("migrate", "--from-template", cwd=old_trip / "research")
        self.assertNotEqual(nested.returncode, 0)
        self.assertIn("根目錄", nested.stderr)
        migration = self.ok(self.cli("migrate", "--from-template"))
        self.assertIn("第 4 版：", migration)
        self.assertIn("第 5 版：", migration)
        self.assertEqual(self.ok(self.git("rev-parse", "HEAD")).strip(), original)
        for name in ("data", "core", "README.md", ".codex/agents/old.toml"):
            self.assertFalse((self.root / name).exists(), name)
        self.assertEqual(profile, (self.root / "profile/preferences.md").read_bytes())
        self.assertIn("保留", (self.root / "notes.example").read_text(encoding="utf-8"))
        self.assertIn("額外資料", (self.root / "extra.md").read_text(encoding="utf-8"))
        self.assertTrue((self.root / ".github/workflows/custom.yml").is_file())
        self.assertIn(f"uses: tsunderink/seasoningway@v{__version__}",
                      (self.root / ".github/workflows/check.yml").read_text(encoding="utf-8"))
        self.assertIn("*.example text", (self.root / ".gitattributes").read_text(encoding="utf-8"))
        self.assertNotIn("linguist-generated", (self.root / ".gitattributes").read_text(encoding="utf-8"))
        self.assertEqual((self.root / ".gitignore").read_text(encoding="utf-8"), "__pycache__/\n*.scratch\n/out/\n")
        settings = tomllib.loads((self.root / "seasoningway.toml").read_text(encoding="utf-8"))
        self.assertEqual((settings["engine"], settings["format"], settings["git"]["mode"]),
                         (__version__, FORMAT, "local"))
        trip = self.root / TRIP
        cfg = json.loads((trip / "map/config.json").read_text(encoding="utf-8"))
        self.assertNotIn("preset", cfg)
        for name in (*VIEWS, "map/index.html"):
            self.assertFalse((trip / name).exists(), name)
        for name, original_bytes in caches.items():
            self.assertEqual(original_bytes, (trip / "cache" / name).read_bytes())
            self.assertFalse((trip / "map" / name).exists(), name)
        published = json.loads((trip / "map/published.json").read_text(encoding="utf-8"))
        self.assertEqual(published, {"$schema": schema_url("published"), "slug": "example-city", "version": "v1", "inputs": {}})
        readme = (trip / "README.md").read_text(encoding="utf-8")
        self.assertIn("[bookings.json](bookings.json)", readme)
        self.assertIn("](map/config.json)", readme)
        self.assertFalse((self.root / "out").exists())

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["render"], root=self.root, today=TODAY), 0, out.getvalue())
        output = self.root / "out"
        for name, text in old.items():
            path = output / name if name == "index.md" else output / SLUG / name
            self.assertEqual(path.read_bytes().decode("utf-8"), expected_view(text, name), name)
        map_dir = output / SLUG / "map"
        page = (map_dir / "index.html").read_text(encoding="utf-8")
        preset = json.loads(re.search(r"^const DATA = (.*);$", page, re.MULTILINE).group(1))["trip"]["preset"]
        self.assertEqual(preset, {"2099-01-15": {}, "2099-01-16": {"ids": ["old-town"]},
                                  "2099-01-17": {"ids": ["riverside-path", "example-temple"]}})
        self.assertEqual((map_dir / "roads.txt").read_bytes(), caches["roads.txt"])

        self.assertEqual(self.ok(self.git("config", "--local", "core.hooksPath")).strip(), ".githooks")
        self.ok(self.cli("save"))
        self.assertEqual(self.ok(self.git("rev-parse", "HEAD^")).strip(), original)
        self.ok(self.cli("migrate", "--from-template"))
        self.ok(self.cli("save", "--check"))
        self.assertEqual(self.ok(self.git("status", "--porcelain")), "")

    def test_rewrites_view_links_in_records_and_old_engine_doc_links(self):
        itinerary = self.root / "data" / TRIP / "itinerary.json"
        text = itinerary.read_text(encoding="utf-8")
        itinerary.write_text(text.replace('"plan": "抵達範例市，車站寄放行李"',
                                          '"plan": "抵達範例市，取消期限見 [bookings.md](bookings.md)、[預算](budget.md)"'),
                             encoding="utf-8", newline="\n")
        readme = self.root / "data" / TRIP / "README.md"
        readme.write_text(readme.read_text(encoding="utf-8") +
                          "\n- 地圖說明見 [core/tripmap](../../../core/tripmap/README.md#介面規格)\n",
                          encoding="utf-8", newline="\n")
        research = self.root / "data" / TRIP / "research/lodging.md"
        research.write_text(research.read_text(encoding="utf-8") +
                            "\n開車時數的算法見 [core/tripmap](../../../../core/tripmap/README.md)。\n",
                            encoding="utf-8", newline="\n")
        self.save_legacy()
        self.ok(self.cli("migrate", "--from-template"))  # 研究筆記連到引擎說明，不要求「查詢於」
        self.assertIn("](https://github.com/tsunderink/seasoningway/blob/main/docs/tripmap.md)",
                      (self.root / TRIP / "research/lodging.md").read_text(encoding="utf-8"))
        plan = json.loads((self.root / TRIP / "itinerary.json").read_text(encoding="utf-8"))["days"][0]["plan"]
        self.assertEqual(plan, "抵達範例市，取消期限見 [bookings.json](bookings.json)、[預算](budget.json)")
        self.assertIn("[core/tripmap](https://github.com/tsunderink/seasoningway/blob/main/docs/tripmap.md#介面規格)",
                      (self.root / TRIP / "README.md").read_text(encoding="utf-8"))

    def test_conflict_and_failed_validation_leave_original_files_intact(self):
        collision = self.write("profile/preferences.md", "# 根目錄已存在\n")
        before = (self.root / "data/trips.toml").read_bytes()
        result = self.cli("migrate", "--from-template")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("搬移衝突", result.stderr)
        self.assertEqual(collision.read_text(encoding="utf-8"), "# 根目錄已存在\n")
        collision.unlink()
        collision.parent.rmdir()
        bad = self.write(f"data/{TRIP.as_posix()}/budget.json", "{")
        result = self.cli("migrate", "--from-template")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("JSON 格式錯誤", result.stderr)
        self.assertEqual(bad.read_text(encoding="utf-8"), "{")
        self.assertEqual((self.root / "data/trips.toml").read_bytes(), before)
        self.assertFalse((self.root / "seasoningway.toml").exists())
        self.assertTrue((self.root / "core/trips.py").exists())
        self.assertFalse(list(self.root.glob(".seasoningway-migrate-*")))

    def test_protects_uncommitted_engine_files(self):
        extra = self.write("core/local-notes.md", "私用筆記\n")
        self.write("AGENTS.md", "# 尚未保存的改動\n")
        result = self.cli("migrate", "--from-template")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("core/local-notes.md", result.stderr)
        self.assertIn("AGENTS.md", result.stderr)
        self.assertTrue(extra.is_file())
        self.assertTrue((self.root / "data/trips.toml").is_file())

    def test_rolls_back_data_and_hook_config_when_install_fails(self):
        old_replace = Path.replace
        blocked = self.root / "core"

        def replace(path, destination):
            if path == blocked:
                raise OSError("測試無法移除舊引擎")
            return old_replace(path, destination)

        with patch.object(Path, "replace", replace), contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(cli.main(["migrate", "--from-template"], root=self.root, today=TODAY), 1)
        self.assertIn("測試無法移除舊引擎", errors.getvalue())
        self.assertEqual(self.ok(self.git("config", "--local", "core.hooksPath")).strip(), "core/githooks")
        self.assertTrue((self.root / "data/trips.toml").is_file())
        self.assertEqual((self.root / "AGENTS.md").read_text(encoding="utf-8"), "# 舊模板規則\n")
        self.assertFalse((self.root / "seasoningway.toml").exists())
        self.assertEqual(self.ok(self.git("status", "--porcelain")), "")
