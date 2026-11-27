"""資料 repo 的引擎測試。"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from unittest import mock
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from seasoningway import __version__, budget, build, checks, cli, common, config, gitops, pii, project, records, trips, views

FIXTURES = Path(__file__).with_name("fixtures")

TODAY = date(2026, 11, 17)
FX = [{"currency": "JPY", "rate": 0.21, "as_of": "2026-11-17", "source": {"url": "https://example.com/fx"}}]


def supports_directory_symlinks() -> bool:
    if os.name != "nt":
        return True
    with tempfile.TemporaryDirectory() as temp:
        target = Path(temp) / "target"
        target.mkdir()
        try:
            (Path(temp) / "link").symlink_to(target, target_is_directory=True)
        except OSError as error:
            if error.winerror in (50, 1314):  # 檔案系統不支援，或目前沒有建立 symlink 的權限
                return False
            raise
    return True


def item(category: str, name: str, amount, currency: str, qty=None, scope=None, status="預估", as_of="2026-11-17", **more) -> dict:
    """budget.json 的一筆（測試用）。"""
    out = {"category": category, "item": name, "unit_amount": amount, "currency": currency}
    out |= {k: v for k, v in (("qty", qty), ("scope", scope)) if v is not None}
    return out | {"status": status, "as_of": as_of} | more


class ScanTest(unittest.TestCase):
    def kinds(self, text: str) -> list[str]:
        return [kind for _, kind in pii.scan_text(text)]

    def test_detects_personal_data(self):
        cases = {
            "卡號 4111 1111 1111 1111": "信用卡號",
            "4111-1111-1111-1111": "信用卡號",
            "4111111111111111": "信用卡號",
            "護照號碼：312345678": "護照號碼",
            "旅券番号: TK1234567": "護照號碼",
            "身分證 A123456789": "身分證字號",
            "Wi-Fi 密碼：abcd1234": "密碼",
            "password: hunter2": "密碼",
            "生日：1990-01-01": "生日",
            "媽媽生日是 3 月 5 日": "生日",
            "0912-345-678": "手機號碼",
            "+886 912 345 678": "手機號碼",
            "090-1234-5678": "手機號碼",
            "手機：0912345678": "電話號碼",
            "訂位代號：ABC123": "訂位代號／確認碼",
            "Confirmation number: 7X9K2P": "訂位代號／確認碼",
            "PNR XYZABC": "訂位代號／確認碼",
            "住家地址：某某路": "住家地址",
            "IC 900101-01-1234": "身分證字號",
            "MyKad: 900101011234": "身分證字號",
            "+60 12-345 6789": "手機號碼",
            "011-1234 5678": "手機號碼",
            "012-345 6789": "手機號碼",
            "012-3456789": "手機號碼",
            "0123456789": "手機號碼",
            "マイナンバー 1234 5678 9012": "個人番號",
            "護照號碼是 A12345678": "護照號碼",
            "Wi-Fi 密碼是 abcd1234": "密碼",
            "Wi-Fi 密碼 abcd1234": "密碼",
            "the password is hunter2": "密碼",
            "訂位代號是 ABC123": "訂位代號／確認碼",
            "確認碼為 XYZ789": "訂位代號／確認碼",
            "訂位代號 abc123": "訂位代號／確認碼",
            "booking ref abc12": "訂位代號／確認碼",
            "住家地址是某某路": "住家地址",
            "訂單編號：12-3456-7890": "訂位代號／確認碼",
            "予約番号 ABC-12345": "訂位代號／確認碼",
            "booking reference: 4012.345.678": "訂位代號／確認碼",
            "PIN 碼：1234": "密碼",
            "PIN碼 1234": "密碼",
            "PIN 1234": "密碼",
            "PIN is 1234": "密碼",
        }
        for text, kind in cases.items():
            with self.subTest(text=text):
                self.assertIn(kind, self.kinds(text))

    def test_allows_ordinary_trip_content(self):
        for text in [
            "2099-01-03 ～ 2099-01-08",
            "| 2026-11-17 | 2026-11-18 |",
            "機票 12,345 TWD，住宿 3000 JPY × 4",
            "航班 BR198 07:30 起飛",
            "店家電話 075-123-4567",
            "店家電話 07-223 4567",
            "01 開頭區碼的市話 011-222-3333、0138-22-3333、0166-23-4567",
            "市話 075-222-1234、IP 電話 050-1234-5678",
            "免費電話 0120-123-456",
            "2026-01-17、1/13 14:00 出發",
            "來源 https://x.com/someone/status/1234567890123456789（查詢於 2026-11-17）",
            "護照效期需 6 個月以上",
            "Wi-Fi 密碼向櫃台索取",
            "媽媽生日那天去吃大餐",
            "訂位代號在訂房 app 裡",
            "信用卡、現金",
            "booking number 請看 email",
            "booking reference number 在確認信",
            "用 password manager 存",
            "密碼（含 Wi-Fi）",
            "訂位代號、確認碼",
            "the password is required at check-in",
            "password is on the card",
            "spin 2000 rpm",
            "Pinterest 1234 收藏",
            "PIN 碼在確認信",
            "確認碼 2026-12-01 前寄到",
            "訂位代號 12-01 前會寄",
        ]:
            with self.subTest(text=text):
                self.assertEqual(self.kinds(text), [])

    def test_text_right_after_a_url_is_still_scanned(self):
        for text in ("[官網](https://example.com)，訂位代號：ABC123",
                     "見 https://example.com，護照號碼：A12345678",
                     "- 住 A（[官網](https://example.com/a?b=1)，媽媽手機0912-345-678）",
                     "https://example.com/x、0912-345-678"):
            with self.subTest(text=text):
                self.assertNotEqual(self.kinds(text), [])

    def test_booking_links_and_more_labels(self):
        for text in ("https://airline.example.com/manage?pnr=ABC123&last=WANG",
                     "[管理訂位](https://booking.example.com/mybooking?confirmation=7X9K2P)",
                     "https://example.com/?tel=0912345678",
                     "https://example.com/?email=me%40mail.example",
                     "https://example.com/?phoneNumber=0912345678&tel_no=0312345678",
                     "https://example.com/?contact_phone=%2B886912345678",
                     "https://example.com/?email_address=a%40b.example",
                     "我家的住址：範例市範例路 1 號",
                     "住家電話：02-2345-6789",
                     "我家電話 (02) 2345-6789",
                     "home phone: +886 2 2345 6789",
                     "緊急聯絡人電話：03-123-4567",
                     "住家電話：（02）2345-6789",
                     "confirmation: ABC123",
                     "住址：範例市範例路 1 號",
                     "居留證 A800000014"):
            with self.subTest(text=text):
                self.assertNotEqual(self.kinds(text), [])
        for text in ("https://www.google.com/maps/search/?api=1&query=35.6,139.7&query_place_id=ChIJ123",
                     "https://example.com/hotel?checkin=2027-01-05&adults=4&ref=nav",
                     "https://example.com/?q=email+template",
                     "confirmation 會寄到信箱",
                     "https://example.com/app?mobile=1&tel=1#mail=x",
                     "店家地址：範例市範例路 1 號",
                     "飯店住址：範例市範例路 1 號",
                     "民宿住址：範例市範例路 1 號",
                     "https://example.com/?hotel=123456&hotel_tel=1",
                     "店家住址：範例市範例路 1 號",
                     "商家住址：範例市範例路 1 號",
                     "店家的住址：範例市範例路 1 號",
                     "溫泉旅館的住址：範例市範例路 1 號",
                     "飯店的住址：範例市範例路 1 號",
                     "緊急聯絡電話：110（警察）",
                     "駐外館處緊急聯絡電話：03-1234-5678",
                     "旅行社 24 小時緊急聯絡電話：02-2345-6789",
                     "飯店室內電話 02-2345-6789",
                     "房間有室內電話：撥 9 接外線"):
            with self.subTest(text=text):
                self.assertEqual(self.kinds(text), [])

    def test_diff_path_reads_the_headers_git_writes(self):
        for header, path in (("+++ b/profile/a.md", "profile/a.md"),
                             ("+++ b/profile/a b.md\t", "profile/a b.md"),  # 含空白：後面多一個 tab
                             ('+++ "b/profile/q\\"uote b.md"\t', 'profile/q"uote b.md'),  # 含引號：加引號、C 的跳脫
                             ('+++ "b/profile/\\303\\251.md"', "profile/é.md"),  # core.quotePath 開著：非 ASCII 寫成八進位
                             ("+++ /dev/null", None)):
            with self.subTest(header=header):
                self.assertEqual(gitops.diff_path(header), path)

    def test_pii_ok_skips_line(self):
        self.assertEqual(self.kinds("民宿電話 0912-345-678 pii-ok"), [])

    def test_public_phone_of_a_place_is_not_personal(self):
        text = '{\n  "contact": {\n    "phone": "090-1234-5678"\n  },\n  "note": "老闆手機 090-1234-5678"\n}'
        self.assertEqual(pii.scan_text(text, "trips/x/places/inn.json"), [(5, "手機號碼")])
        self.assertEqual(pii.scan_text(text, "trips/x/bookings.json"), [(3, "手機號碼"), (5, "手機號碼")])
        one_line = '{"contact": {"address": "某處", "phone": "090-1234-5678"}, "note": "老闆手機 090-1234-5678"}'
        self.assertEqual(pii.scan_text(one_line, "trips/x/places/inn.json"), [(1, "手機號碼")])  # 只有 note 那個
        self.assertEqual(pii.scan_text(one_line.replace("老闆手機 090-1234-5678", ""), "trips/x/places/inn.json"), [])

    def test_brief_trims_next_steps_for_session_start(self):
        self.assertEqual(views.brief("- 讀 [map/README.md](map/README.md) 再改"), "讀 map/README.md 再改")
        long = "- " + "字" * 100
        self.assertEqual(len(views.brief(long)), views.BRIEF_CHARS)
        self.assertTrue(views.brief(long).endswith("…"))

    def test_luhn(self):
        self.assertTrue(pii.luhn_ok("4111111111111111"))
        self.assertFalse(pii.luhn_ok("4111111111111112"))

    def test_dump_json_is_the_record_style(self):
        self.assertEqual(records.dump_json({"金額": Decimal("1.50"), "n": Decimal("3")}), '{\n  "金額": 1.5,\n  "n": 3\n}\n')


class RepoTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        with contextlib.redirect_stdout(io.StringIO()):  # 初始化
            self.assertEqual(cli.main(["migrate"], root=self.root, today=TODAY), 0)
        self.set_config('default_currency = ""', 'default_currency = "TWD"')
        self.trip = project.create_trip(self.root, "2099-01-example-city", "範例市冬遊", 2, None, TODAY)

    def set_config(self, old: str, new: str):
        path = self.root / common.CONFIG
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new), encoding="utf-8")

    def write_record(self, name: str, data: dict):
        (self.trip.path / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def write_budget(self, items: list[dict], rates: list[dict] = FX):
        self.write_record("budget.json", {"rates": rates, "items": items})

    def fx(self, rate, as_of="2026-11-17") -> list[dict]:
        return [{"currency": "JPY", "rate": rate, "as_of": as_of, "source": {"url": "https://example.com/fx"}}]

    def test_new_trip_uses_template_and_default_currency(self):
        self.assertEqual(self.trip.title, "範例市冬遊")
        self.assertEqual(self.trip.currency, "TWD")
        self.assertEqual(self.trip.travelers, 2)
        self.assertTrue((self.trip.path / "research").is_dir())
        self.assertNotIn("{{", (self.trip.path / "README.md").read_text(encoding="utf-8"))
        self.assertIn("範例市冬遊", (self.root / common.INDEX).read_text(encoding="utf-8"))
        with self.assertRaises(common.Problem):
            project.create_trip(self.root, "2099-01-example-city", None, 1, None, TODAY)
        with self.assertRaises(common.Problem):
            project.create_trip(self.root, "example-city", None, 1, None, TODAY)

    def test_migrate_initializes_and_keeps_existing_files(self):
        cfg = config.load_config(self.root)
        self.assertEqual(cfg.engine, __version__)
        self.assertEqual((cfg.format, cfg.default_currency, cfg.git_mode), (config.FORMAT, "TWD", ""))  # git.mode 等使用者決定
        prefs = self.root / "profile" / "preferences.md"
        prefs.write_text("# 偏好\n\n- 不吃生食\n", encoding="utf-8")
        (self.root / "wishlist.md").unlink()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["migrate"], root=self.root, today=TODAY), 0)  # 已經是最新版：不動
        self.assertIn("本來就是", out.getvalue())
        self.assertFalse((self.root / "wishlist.md").exists())
        (self.root / common.CONFIG).unlink()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["migrate"], root=self.root, today=TODAY), 0)  # 沒有設定檔就初始化：補上缺的檔，已經有的不蓋掉
        self.assertTrue((self.root / "wishlist.md").is_file())
        self.assertEqual(prefs.read_text(encoding="utf-8"), "# 偏好\n\n- 不吃生食\n")
        fresh = Path(self.enterContext(tempfile.TemporaryDirectory()))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["--root", str(fresh), "migrate"], today=TODAY), 0)
        for name in ("seasoningway.toml", "profile/preferences.md", "profile/lessons.md", "wishlist.md", common.INDEX, ".gitattributes"):
            self.assertTrue((fresh / name).is_file(), name)
        self.assertEqual(config.load_config(fresh).default_currency, "")

    def test_migrate_refuses_versions_it_cannot_upgrade(self):
        self.set_config(f"format = {config.FORMAT}", f"format = {config.FORMAT + 1}")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(cli.main(["migrate"], root=self.root, today=TODAY), 1)
        self.assertIn("更新引擎", err.getvalue())
        if project.OLDEST > 1:
            self.set_config(f"format = {config.FORMAT + 1}", f"format = {project.OLDEST - 1}")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(cli.main(["migrate"], root=self.root, today=TODAY), 1)
            self.assertIn("舊版的引擎", err.getvalue())

    def test_commands_ask_to_initialize_first(self):
        fresh = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for command in (["build"], ["budget", "2099-01-example-city"], ["new", "2099-01-example-city"]):
            with self.subTest(command=command), contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(cli.main(["--root", str(fresh), *command], today=TODAY), 1)
                self.assertIn("先執行 seasoningway migrate", err.getvalue())

    def test_config_problems(self):
        self.assertEqual(config.config_problems(self.root)[1], [])
        self.set_config('# mode = "main"', 'mode = "yolo"\nextra = 1')
        joined = "\n".join(checks.run_check(self.root, TODAY)[0])
        self.assertIn("seasoningway.toml：git.mode：'yolo' is not one of", joined)
        self.assertIn("seasoningway.toml：git：Additional properties are not allowed ('extra'", joined)
        (self.root / common.CONFIG).unlink()
        problems = checks.run_check(self.root, TODAY)[0]
        self.assertEqual(len(problems), 1)  # 還沒初始化時只提示 migrate，不列它的後果
        self.assertIn(f"還沒有 {common.CONFIG}", problems[0])

    def test_config_requires_a_pinned_engine_version(self):
        self.set_config(f'engine = "{__version__}"\n', '')
        self.assertIn("'engine' is a required property", "\n".join(config.config_problems(self.root)[1]))
        path = self.root / common.CONFIG
        path.write_text('engine = "main"\n' + path.read_text(encoding="utf-8"), encoding="utf-8")
        self.assertIn("seasoningway.toml：engine", "\n".join(config.config_problems(self.root)[1]))

    def test_config_of_another_version_is_not_validated_against_this_schema(self):
        self.set_config('# mode = "main"', 'mode = "main"')
        path = self.root / common.CONFIG
        path.write_text(path.read_text(encoding="utf-8") + "\n[legacy]\nold = 1\n", encoding="utf-8")  # 這版 schema 不認得的欄位
        cfg, problems = config.config_problems(self.root)
        self.assertIn("Additional properties are not allowed ('legacy'", "\n".join(problems))
        self.assertEqual(cfg.git_mode, "main")  # hooks 照樣照 git.mode 做
        self.assertEqual(config.load_config(self.root, validate=False).git_mode, "main")

        def upgrade(root: Path) -> list[str]:  # 假的新版引擎：拿掉舊欄位
            path.write_text(path.read_text(encoding="utf-8").replace("\n[legacy]\nold = 1\n", ""), encoding="utf-8")
            return ["拿掉 legacy"]
        new = config.FORMAT + 1
        with mock.patch.object(config, "FORMAT", new), mock.patch.dict(project.MIGRATIONS, {new: upgrade}):
            self.assertEqual(config.config_problems(self.root)[1],
                             [f"資料格式是第 {new - 1} 版，這版引擎要第 {new} 版：執行 seasoningway migrate"])
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(cli.main(["migrate"], root=self.root, today=TODAY), 0)
            self.assertIn("拿掉 legacy", out.getvalue())
            self.assertEqual(config.config_problems(self.root)[1], [])

        shutil.copy(project.TEMPLATES / "project" / common.CONFIG, path)  # 直接複製範本（format = 0）
        self.assertEqual(config.config_problems(self.root)[1], [f"{common.CONFIG} 還沒初始化：執行 seasoningway migrate"])
        for command in (["build"], ["new", "2099-02-example-city", "--currency", "TWD"]):  # 讀資料的指令只提示 migrate
            with self.subTest(command=command), contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(cli.main(command, root=self.root, today=TODAY), 1)
            self.assertIn("還沒初始化：先執行 seasoningway migrate", err.getvalue())
        self.assertFalse((self.root / "trips" / "2099-02-example-city").exists())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["migrate"], root=self.root, today=TODAY), 0)
        self.assertEqual(config.config_problems(self.root)[1], [])

    def test_wrongly_typed_records_are_reported_without_crashing(self):
        itinerary = self.trip.path / "itinerary.json"
        for bad in ('{"days": [1]}', '{"days": {"a": 1}}', '[]', '{"days": [{"places": 5}]}', '{"days": [{"stay_place": ["x"]}]}'):
            with self.subTest(bad=bad):
                itinerary.write_text(bad, encoding="utf-8")
                self.assertTrue(any("itinerary.json" in p for p in checks.run_check(self.root, TODAY)[0]))
        (self.trip.path / "places").mkdir(exist_ok=True)
        (self.trip.path / "places" / "a.json").write_text("[]", encoding="utf-8")
        self.assertTrue(any("places/a.json" in p for p in checks.run_check(self.root, TODAY)[0]))

    def test_unset_git_mode_only_warns(self):
        problems, warnings = checks.run_check(self.root, TODAY)
        self.assertEqual(problems, [])  # ✗ 會讓 Stop hook 擋住回合，連問使用者的機會都沒有
        self.assertIn("還沒設定 git.mode", "\n".join(warnings))
        self.set_config('# mode = "main"', 'mode = "main"')
        self.assertNotIn("git.mode", "\n".join(checks.run_check(self.root, TODAY)[1]))

    def test_roles_and_publish_come_from_config(self):
        readme = self.trip.path / "README.md"
        readme.write_text(readme.read_text(encoding="utf-8").replace("## 下一步\n", "## 下一步\n\n- 媽媽 查護照效期\n"), encoding="utf-8")
        self.assertIn("「下一步」要先寫誰做", "\n".join(checks.run_check(self.root, TODAY)[1]))
        self.set_config('roles = ["我", "朋友"]', 'roles = ["我", "媽媽"]')
        self.assertNotIn("「下一步」要先寫誰做", "\n".join(checks.run_check(self.root, TODAY)[1]))
        page = self.trip.path / "map"
        page.mkdir()
        (page / "published.json").write_text('{"slug": "s", "version": "", "include": ["*.html"], "files": {"a.html": "x"}}', encoding="utf-8")
        self.assertIn("上次發布", "\n".join(checks.run_check(self.root, TODAY)[1]))
        self.set_config('target = "artifact"', 'target = "none"')
        self.assertNotIn("上次發布", "\n".join(checks.run_check(self.root, TODAY)[1]))

    def test_demo_example_passes_check(self):
        self.assertEqual(checks.run_check(FIXTURES / "demo", TODAY), ([], []))

    def test_budget_math(self):
        self.write_budget([
            item("住宿", "車站附近飯店", 12000, "JPY", 4, status="已訂"),
            item("餐飲", "每日餐費概算", 5000, "JPY", 5, "每人"),
            item("交通", "機票,來回", 8000, "TWD", scope="每人", source={"url": "https://example.com", "title": "航空公司"}),
            item("餐飲", "旅後概算", 25000, "TWD", status="實付", as_of="2099-01-20"),
        ])
        b = budget.compute_budget(trips.load_trip(self.root, "2099-01-example-city"), self.root)
        # 12000×4×0.21=10080；5000×5×2×0.21=10500；8000×2=16000
        self.assertEqual([l.base for l in b.lines], [Decimal("10080.00"), Decimal("10500.00"), Decimal(16000), Decimal(25000)])
        self.assertEqual(budget.total(b.lines, None, budget.PLAN_STATUSES), Decimal("36580.00"))
        text = budget.render_budget(b)
        self.assertIn("| **合計** | **26,500** | **10,080** | **36,580** | **18,290** |", text)
        self.assertIn("| 餐飲 | 10,500 | 25,000 | +14,500 |", text)
        self.assertIn("5,000 JPY × 5 × 2 人", text)
        self.assertIn("| [航空公司](https://example.com) |", text)

    def test_show_also_lists_second_currency(self):
        self.write_budget([item("住宿", "飯店", 10000, "JPY", 2)], self.fx(0.25))
        readme = self.trip.path / "README.md"
        readme.write_text(readme.read_text(encoding="utf-8").replace("show_also:", "show_also: JPY"), encoding="utf-8")
        b = budget.compute_budget(trips.load_trip(self.root, "2099-01-example-city"), self.root)
        text = budget.render_budget(b)
        # 10000×2×0.25=5000 TWD，換回 20000 JPY，每人 10000 JPY
        self.assertIn("| **合計** | **5,000** | **0** | **5,000** | **2,500** | **20,000** | **10,000** |", text)
        self.assertIn("| 小計（JPY） | 每人（JPY） |", text)
        self.assertIn("| 5,000 | 20,000 | 2026-11-17 |", text)
        self.assertIn("  約 20,000 JPY（每人 10,000 JPY）", budget.budget_summary(b))
        readme.write_text(readme.read_text(encoding="utf-8").replace("show_also: JPY", "show_also: KRW"), encoding="utf-8")
        with self.assertRaisesRegex(common.Problem, "show_also"):
            budget.compute_budget(trips.load_trip(self.root, "2099-01-example-city"), self.root)

    def test_budget_target_shows_gap(self):
        self.write_budget([item("住宿", "飯店", 10000, "JPY", 2),
                           item("交通", "租車（已訂，金額待補）", 4000, "JPY", 1),
                           item("門票活動", "門票", 2000, "TWD", 1, status="已訂")], self.fx(0.25))
        readme = self.trip.path / "README.md"
        readme.write_text(readme.read_text(encoding="utf-8").replace("budget_target:", "budget_target: 4,000"), encoding="utf-8")
        b = budget.compute_budget(trips.load_trip(self.root, "2099-01-example-city"), self.root)
        line = "預算目標 4,000 TWD（README 開頭的 budget_target），計畫超出 4,000 TWD，是目標的 200%"
        self.assertIn(line, budget.render_budget(b))
        self.assertIn("  " + line, budget.budget_summary(b))
        # 已訂 2000 + 金額待補 4000×0.25=1000
        self.assertIn("  已訂合計 3,000 TWD（含 1 筆金額待補的估價），是目標的 75%", budget.budget_summary(b))
        readme.write_text(readme.read_text(encoding="utf-8").replace("budget_target: 4,000", "budget_target: 很多"), encoding="utf-8")
        problems, _ = checks.run_check(self.root, TODAY)
        self.assertTrue(any("budget_target：'很多' does not match" in p for p in problems), problems)
        readme.write_text(readme.read_text(encoding="utf-8").replace("budget_target: 很多", "budget_target: 0"), encoding="utf-8")
        self.assertIn("budget_target 要是正數", "\n".join(checks.run_check(self.root, TODAY)[0]))

    def test_budget_rejects_missing_rate_and_bad_status(self):
        self.write_budget([item("餐飲", "點心", 500, "KRW")])
        with self.assertRaisesRegex(common.Problem, "items\\[0\\]（點心）：rates 沒有 KRW 的匯率"):
            budget.compute_budget(self.trip, self.root)
        self.write_budget([item("餐飲", "點心", 500, "JPY", status="大概", confirmation="X")])
        with self.assertRaises(common.Problem) as e:
            budget.compute_budget(self.trip, self.root)
        self.assertIn("budget.json：items[0]（點心）：Additional properties are not allowed ('confirmation'", str(e.exception))
        self.assertIn("budget.json：items[0].status（點心）：'大概' is not one of", str(e.exception))

    def test_stale_estimates_warn_only_for_active_trips(self):
        self.write_budget([item("住宿", "飯店", 10000, "JPY", as_of="2026-09-01")], self.fx(0.21, "2026-09-01"))
        b = budget.compute_budget(self.trip, self.root)
        self.assertEqual(len(budget.budget_warnings(b, TODAY)), 2)
        b.trip.meta["status"] = "已結束"
        self.assertEqual(budget.budget_warnings(b, TODAY), [])

    def test_check_flags_stale_outputs_research_and_pii(self):
        problems, _ = checks.run_check(self.root, TODAY)
        self.assertEqual(problems, [])
        self.write_budget([item("住宿", "飯店", 10000, "JPY")])
        (self.trip.path / "research" / "transport.md").write_text(
            "- 範例周遊券 3 日券（[官網](https://example.com)）\n"
            "- 範例周遊券 1 日券（[官網](https://example.com)，查詢於 2026-11-17）\n",
            encoding="utf-8",
        )
        self.write_record("bookings.json", {"items": [{"id": "inn", "item": "民宿", "status": "待訂", "note": "老闆手機 0912-345-678"}]})
        problems, _ = checks.run_check(self.root, TODAY)
        joined = "\n".join(problems)
        self.assertIn("budget.md 不是最新，請執行 seasoningway build", joined)
        self.assertIn("bookings.md 不是最新", joined)
        self.assertIn("transport.md:1", joined)
        self.assertNotIn("transport.md:2", joined)
        self.assertIn("bookings.json:7：疑似手機號碼", joined)

    def test_file_problems_checks_nested_research_notes(self):
        note = self.trip.path / "research" / "a" / "b" / "c.md"
        note.parent.mkdir(parents=True)
        note.write_text("- 票價（[官網](https://example.com)）\n", encoding="utf-8")
        self.assertIn("c.md:1", "\n".join(checks.file_problems(self.root, note)))

    def test_pii_scan_skips_generated_files(self):
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        (self.root / ".gitattributes").write_text("trips/*/map/index.html linguist-generated\n", encoding="utf-8")
        (self.trip.path / "map").mkdir()
        (self.trip.path / "map" / "index.html").write_text("手機 0912-345-678\n", encoding="utf-8")
        (self.trip.path / "map" / "template.html").write_text("手機 0912-345-678\n", encoding="utf-8")
        joined = "\n".join(pii.check_pii(self.root))
        self.assertNotIn("index.html", joined)
        self.assertIn("template.html:1：疑似手機號碼", joined)

    def test_hygiene_links_citations_orphans(self):
        self.set_config('# mode = "main"', 'mode = "main"')
        problems, warnings = checks.run_check(self.root, TODAY)
        self.assertEqual((problems, warnings), ([], []))
        cite = "（[官網](https://example.com/a)，查詢於 2026-11-17）"
        (self.trip.path / "research" / "shrine.md").write_text(
            "## 冬天\n\n" + "".join(f"- 事實 {i}{cite}\n" for i in range(3)), encoding="utf-8")
        readme = self.trip.path / "README.md"
        readme.write_text(readme.read_text(encoding="utf-8").replace(
            "## 已決定\n", "## 已決定\n\n- 2026-11-17：住範例市（2026-11-18 改，見下）\n- 見 [舊筆記](research/gone.md)、`[範例](URL)`\n"
        ).replace("## 下一步\n", "## 下一步\n\n- 使用者 12/1 訂票\n- 使用者看確認信：訂單含不含保險\n- 查營業時間\n- 使用者 12/2 訂門票（[bookings.md](bookings.md)）\n"
                    "- 我 12/3 訂車\n- Claude 查哪天開放預訂\n- Codex 整理要訂的票\n"),
            encoding="utf-8")
        problems, warnings = checks.run_check(self.root, TODAY)
        self.assertEqual(len(problems), 1)
        self.assertIn("README.md:", problems[0])
        self.assertIn("research/gone.md", problems[0])
        joined = "\n".join(warnings)
        self.assertIn("shrine.md「冬天」：同一來源引用了 3 次", joined)
        self.assertIn("shrine.md：沒有任何檔案連到它", joined)
        self.assertIn("「已決定」有「見下」", joined)
        self.assertIn("「下一步」要先寫誰做", joined)
        self.assertEqual(joined.count("「下一步」要先寫誰做"), 1)
        self.assertEqual(joined.count("要訂的東西寫進 bookings.json"), 2)  # 「使用者」和角色代稱「我」，助理（Claude、Codex）不算
        self.assertEqual(joined.count("「已決定」要以決定的日期開頭"), 1)

    def test_orphan_notes_follow_links_into_subfolders_and_stay_within_the_trip(self):
        self.set_config('# mode = "main"', 'mode = "main"')
        cite = "（[官網](https://example.com/a)，查詢於 2026-11-17）"
        for rel in ("food/ramen.md", "food/soba.md", "lodging.md"):
            note = self.trip.path / "research" / rel
            note.parent.mkdir(parents=True, exist_ok=True)
            note.write_text(f"- 事實{cite}\n", encoding="utf-8")
        (self.trip.path / "research" / "food" / "ramen.md").write_text(f"- 事實{cite}\n- 另見 [蕎麥麵](soba.md)\n", encoding="utf-8")
        readme = self.trip.path / "README.md"
        readme.write_text(readme.read_text(encoding="utf-8").replace("## 已決定\n", "## 已決定\n\n- 2026-11-17：見 [拉麵](research/food/ramen.md)\n"),
                          encoding="utf-8")
        other = project.create_trip(self.root, "2099-02-other", "另一趟", 1, "TWD", TODAY)
        (other.path / "research" / "x.md").write_text("- 見 research/lodging.md\n", encoding="utf-8")  # 別趟同名的筆記不算
        warnings = "\n".join(checks.run_check(self.root, TODAY)[1])
        self.assertNotIn("ramen.md：沒有任何檔案連到它", warnings)
        self.assertNotIn("soba.md：沒有任何檔案連到它", warnings)
        self.assertIn(f"{self.trip.slug}/research/lodging.md：沒有任何檔案連到它", warnings)

    def test_encoded_links_and_nested_notes_budget(self):
        self.set_config('# mode = "main"', 'mode = "main"')
        cite = "（[官網](https://example.com/a)，查詢於 2026-11-17）"
        (self.trip.path / "research" / "my note.md").write_text(f"- 事實{cite}\n", encoding="utf-8")
        deep = self.trip.path / "research" / "food" / "long.md"
        deep.parent.mkdir()
        deep.write_text(f"- 事實{cite}\n" + "字" * 9500 + "\n", encoding="utf-8")
        readme = self.trip.path / "README.md"
        readme.write_text(readme.read_text(encoding="utf-8").replace(
            "## 已決定\n", "## 已決定\n\n- 2026-11-17：見 [筆記](research/my%20note.md)、[長筆記](research/food/long.md)\n"), encoding="utf-8")
        problems, warnings = checks.run_check(self.root, TODAY)
        self.assertEqual(problems, [])  # %20 解開後找得到檔案
        self.assertNotIn("my note.md：沒有任何檔案連到它", "\n".join(warnings))
        self.assertIn("long.md：9,5", "\n".join(warnings))
        self.assertIn("超過 9,000 字的預算", "\n".join(warnings))  # research 子資料夾的筆記也用研究筆記的上限

    def test_leftover_conflict_markers_are_problems(self):
        readme = self.trip.path / "README.md"
        readme.write_text(readme.read_text(encoding="utf-8").replace(
            "## 已決定\n", "## 已決定\n\n<<<<<<< HEAD\n- 2026-11-17：住 A\n=======\n- 2026-11-17：住 B\n>>>>>>> origin/main\n"), encoding="utf-8")
        problems = "\n".join(checks.run_check(self.root, TODAY)[0])
        self.assertRegex(problems, r"README\.md:\d+：還有合併衝突的標記")

    def test_scripts_without_a_readme_are_flagged(self):
        tool = self.trip.path / "tools" / "fetch.py"
        tool.parent.mkdir()
        tool.write_text("", encoding="utf-8")
        self.assertIn("tools/fetch.py：同資料夾沒有 README.md", "\n".join(checks.run_check(self.root, TODAY)[1]))

    def test_stale_research_warns_only_when_linked_from_open_decisions(self):
        note = self.trip.path / "research" / "hotel.md"
        note.write_text("- 房價 ¥10,000（[官網](https://example.com)，查詢於 2026-09-01）\n", encoding="utf-8")
        readme = self.trip.path / "README.md"
        text = readme.read_text(encoding="utf-8")
        readme.write_text(text.replace("## 已決定\n", "## 已決定\n\n- 2026-11-17：見 [hotel](research/hotel.md)\n"), encoding="utf-8")
        _, warnings = checks.run_check(self.root, TODAY)
        self.assertNotIn("做決定前重查", "\n".join(warnings))
        readme.write_text(text.replace("## 待決定\n", "## 待決定\n\n- 住哪間（[hotel](research/hotel.md)）\n"), encoding="utf-8")
        _, warnings = checks.run_check(self.root, TODAY)
        self.assertIn("hotel.md：「待決定」用到它，有 1 處引用超過 30 天（最舊 2026-09-01）", "\n".join(warnings))

    def test_entry_note_required_close_to_departure(self):
        readme = self.trip.path / "README.md"
        readme.write_text(readme.read_text(encoding="utf-8").replace("start:", "start: 2027-02-01"), encoding="utf-8")
        self.assertNotIn("入境規定", "\n".join(checks.run_check(self.root, TODAY)[1]))  # 還有 76 天
        readme.write_text(readme.read_text(encoding="utf-8").replace("start: 2027-02-01", "start: 2026-12-20"), encoding="utf-8")
        self.assertIn("再 33 天出發，research/ 還沒有入境規定", "\n".join(checks.run_check(self.root, TODAY)[1]))
        (self.trip.path / "research" / "entry.md").write_text("# 入境\n", encoding="utf-8")
        self.assertNotIn("入境規定", "\n".join(checks.run_check(self.root, TODAY)[1]))
        soon = TODAY + timedelta(days=33 - 10)  # 出發前 10 天
        self.assertIn("入境規定的筆記最後查詢於 （沒有日期）", "\n".join(checks.run_check(self.root, soon)[1]))
        (self.trip.path / "research" / "entry.md").write_text("# 入境\n- 免簽（[官網](https://example.com)，查詢於 2026-11-01）\n", encoding="utf-8")
        self.assertIn("最後查詢於 2026-11-01", "\n".join(checks.run_check(self.root, soon)[1]))
        (self.trip.path / "research" / "entry.md").write_text(f"# 入境\n- 免簽（[官網](https://example.com)，查詢於 {soon}）\n", encoding="utf-8")
        self.assertNotIn("入境規定", "\n".join(checks.run_check(self.root, soon)[1]))

    def test_impossible_citation_dates_are_reported_not_crashed_on(self):
        note = self.trip.path / "research" / "hotel.md"
        note.write_text("# 住宿\n- 一晚 9,000 日圓（[官網](https://example.com)，查詢於 2026-02-30）\n", encoding="utf-8")
        readme = self.trip.path / "README.md"
        readme.write_text(readme.read_text(encoding="utf-8").replace("## 待決定\n", "## 待決定\n\n- 選住宿（[筆記](research/hotel.md)）\n"),
                          encoding="utf-8")
        problems, warnings = checks.run_check(self.root, TODAY)
        self.assertIn("hotel.md:2：「查詢於 2026-02-30」不是存在的日期", "\n".join(problems))
        self.assertEqual(checks.file_problems(self.root, note), [p for p in problems if "hotel.md" in p])

    def test_list_keeps_going_past_a_broken_trip(self):
        readme = self.trip.path / "README.md"
        readme.write_text(readme.read_text(encoding="utf-8").replace("budget_target:", "budget_target: 0"), encoding="utf-8")
        self.write_budget([])
        project.create_trip(self.root, "2099-02-other-city", "另一趟", 1, None, TODAY)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(views.print_active_trips(self.root), 0)
        self.assertIn("預算算不出來：", out.getvalue())
        self.assertIn("另一趟", out.getvalue())

    def test_check_reports_files_that_are_not_utf8(self):
        (self.trip.path / "research" / "old.md").write_bytes("# 舊筆記\n".encode("big5"))
        problems, _ = checks.run_check(self.root, TODAY)
        self.assertEqual(problems, [f"trips/{self.trip.slug}/research/old.md：不是 UTF-8，用 UTF-8 重新存檔"])

    def test_trip_end_must_not_be_before_start(self):
        readme = self.trip.path / "README.md"
        readme.write_text(readme.read_text(encoding="utf-8").replace("start:", "start: 2099-03-10").replace("end:", "end: 2099-03-01"),
                          encoding="utf-8")
        self.assertIn("README.md：end（2099-03-01）早於 start（2099-03-10）", "\n".join(checks.run_check(self.root, TODAY)[0]))

    def test_unreadable_published_state_is_reported_and_rebuilt(self):
        page = self.trip.path / "map"
        page.mkdir()
        (page / "index.html").write_text("v1", encoding="utf-8")
        (page / "published.json").write_text('<<<<<<< HEAD\n{"slug": "s"}\n=======\n>>>>>>> x\n', encoding="utf-8")
        self.assertIn("map/published.json 讀不了（例如合併衝突", "\n".join(checks.run_check(self.root, TODAY)[0]))
        for include in ('"index.html"', '["/x"]', '[""]', '["../x"]', '[]'):  # 格式不對：報 ✗，不丟 traceback
            with self.subTest(include=include):
                (page / "published.json").write_text(f'{{"slug": "s", "version": "v1", "include": {include}, "files": {{}}}}\n',
                                                     encoding="utf-8")
                self.assertIn("map/published.json 讀不了", "\n".join(checks.run_check(self.root, TODAY)[0]))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(cli.main(["published", f"trips/{self.trip.slug}/map", "v2", "--slug", "s", "--include", ""],
                                        root=self.root, today=TODAY), 1)
        self.assertIn("--include 的樣式要是資料夾裡的相對路徑", err.getvalue())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["published", f"trips/{self.trip.slug}/map", "v2", "--slug", "s", "--include", "*.html"],
                                        root=self.root, today=TODAY), 0)
        self.assertEqual(checks.run_check(self.root, TODAY)[0], [])

    @unittest.skipUnless(supports_directory_symlinks(), "目前的 Windows 環境無法建立目錄符號連結")
    def test_published_accepts_symlinked_root(self):
        page = self.trip.path / "map"
        page.mkdir()
        (page / "index.html").write_text("v1", encoding="utf-8")
        link = self.root.parent / (self.root.name + "-link")
        link.symlink_to(self.root, target_is_directory=True)
        self.addCleanup(link.unlink)
        with contextlib.redirect_stdout(io.StringIO()):  # 從符號連結的路徑跑也認得出資料夾
            self.assertEqual(cli.main(["published", f"trips/{self.trip.slug}/map", "v2", "--slug", "s", "--include", "*.html"],
                                        root=link, today=TODAY), 0)
        self.assertEqual(checks.run_check(self.root, TODAY)[0], [])

    def test_unpublished_artifact_files_warn(self):
        page = self.trip.path / "map"
        page.mkdir()
        (page / "index.html").write_text("v1", encoding="utf-8")
        folder = f"trips/{self.trip.slug}/map"
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(cli.main(["published", folder, "v1"], root=self.root, today=TODAY), 1)
            self.assertEqual(cli.main(["published", "core", "v1", "--slug", "s", "--include", "*"], root=self.root, today=TODAY), 1)
        self.assertIn("沒有可用的 published.json：加 --slug", err.getvalue())
        with contextlib.redirect_stdout(io.StringIO()):  # 第一次發布
            self.assertEqual(cli.main(["published", folder, "v1", "--slug", "s", "--include", "*.html"], root=self.root, today=TODAY), 0)
        self.assertEqual(json.loads((page / "published.json").read_text(encoding="utf-8"))["include"], ["*.html"])
        self.assertNotIn("上次發布", "\n".join(checks.run_check(self.root, TODAY)[1]))
        (page / "index.html").write_text("v2", encoding="utf-8")
        (page / "new.html").write_text("x", encoding="utf-8")
        self.assertIn("index.html、new.html和上次發布的不一樣", "\n".join(checks.run_check(self.root, TODAY)[1]))
        (page / "README.md").write_text("| `build.py` | 產生 |\n", encoding="utf-8")
        (page / "build.py").write_text("", encoding="utf-8")
        (page / "old.py").write_text("", encoding="utf-8")
        joined = "\n".join(checks.run_check(self.root, TODAY)[1])
        self.assertIn("map/old.py：同資料夾的 README.md 沒提到它", joined)
        self.assertNotIn("build.py：同資料夾", joined)

    def test_check_runs_trip_build_scripts(self):
        tool = self.trip.path / "map"
        tool.mkdir()
        (tool / "build.py").write_text(
            "import sys\n"
            "if '--check' in sys.argv and open('data.txt').read() != 'ok':\n"
            "    print('index.html 不是最新'); sys.exit(1)\n",
            encoding="utf-8",
        )
        (tool / "data.txt").write_text("ok", encoding="utf-8")
        problems, _ = checks.run_check(self.root, TODAY)
        self.assertEqual(problems, [])
        (tool / "data.txt").write_text("stale", encoding="utf-8")
        problems, _ = checks.run_check(self.root, TODAY)
        self.assertEqual(len(problems), 1)
        self.assertIn("map/build.py --check 沒通過：index.html 不是最新", problems[0])
        (tool / "build.py").write_text(
            "import sys\n"
            "if '--check' in sys.argv: sys.exit(open('data.txt').read() != 'ok')\n"
            "open('data.txt', 'w').write('ok')\n",
            encoding="utf-8",
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(build.cmd_build(None, self.root, TODAY), 0)
        problems, _ = checks.run_check(self.root, TODAY)
        self.assertEqual(problems, [])

    def test_bookings_view_sorts_and_warns(self):
        self.write_record("bookings.json", {"note": "取消期限是當地時間 23:59。", "items": [
            {"id": "hotel", "item": "1/3 飯店", "date": "2099-01-03", "status": "已訂", "status_note": "1/1 前免費取消"},
            {"id": "museum", "item": "博物館門票", "status": "待訂", "when": "12/1 開賣", "book_on": "2026-12-01",
             "platform": "[官網](https://example.com)"},
            {"id": "inn", "item": "1/1 旅館", "date": "2099-01-01", "status": "已訂", "platform": "Booking.com"},
            {"id": "late", "item": "晚到的票", "status": "待訂", "book_on": "2026-11-01"},
            {"id": "asap", "item": "盡早訂的", "status": "看需要", "when": "盡早"},
            {"id": "skip", "item": "機場巴士", "status": "不需要", "status_note": "改搭計程車"},
        ]})
        trip = trips.load_trip(self.root, self.trip.slug)
        text = views.render_bookings(trip, json.loads((self.trip.path / "bookings.json").read_text(encoding="utf-8")))
        self.assertIn("取消期限是當地時間 23:59。\n\n## 待訂", text)
        rows = [l.split(" | ")[0][2:] for l in text.splitlines() if l.startswith("| ") and not l.startswith("| 項目")]
        self.assertEqual(rows, ["盡早訂的", "晚到的票", "博物館門票", "1/1 旅館", "1/3 飯店", "機場巴士"])
        self.assertIn("| 1/3 飯店 | 已訂（1/1 前免費取消） | （待補） |  |", text)
        self.assertIn("| 晚到的票 | — | 待訂 | — |  |", text)
        self.assertIn("| 機場巴士 | 改搭計程車 |", text)
        _, _, warnings = views.trip_views(trip, self.root, TODAY)
        self.assertEqual(len(warnings), 1)
        self.assertIn("「晚到的票」打算 2026-11-01 訂，還是待訂", warnings[0])

    def test_itinerary_view_and_link_check(self):
        self.write_record("itinerary.json", {"summary": "草案。", "days": [
            {"date": "2099-01-05", "day_note": "連假", "plan": "老街（[筆記](research/old-town.md)）", "stay": "飯店 A", "travel": "巴士 30 分鐘"},
            {"date": "2099-01-06", "plan": "範例寺", "stay": "飯店 A"},
            {"date": "2099-01-07", "plan": "回家"},
        ], "notes": "交通都是粗估。"})
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(build.cmd_build(None, self.root, TODAY), 0)  # 壞連結由 check 擋
        text = (self.trip.path / "itinerary.md").read_text(encoding="utf-8")
        self.assertIn("草案。\n\n| 日期 | 去哪 | 住 | 交通 |", text)
        self.assertIn("| 1/5（一，連假） | 老街（[筆記](research/old-town.md)） | 飯店 A | 巴士 30 分鐘 |", text)
        self.assertIn("| 1/6（二） | 範例寺 | 同上 | — |", text)
        self.assertIn("| 1/7（三） | 回家 | — | — |\n\n交通都是粗估。\n", text)
        problems, _ = checks.run_check(self.root, TODAY)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("itinerary.md:9：連結 research/old-town.md 指向不存在的檔案（改 itinerary.json）", problems[0])

    def test_records_rules_beyond_schema(self):
        self.write_record("bookings.json", {"items": [{"id": "a", "item": "x", "status": "待訂"},
                                                     {"id": "a", "item": "y", "status": "已訂", "date": "2027-02-30"}]})
        self.write_record("itinerary.json", {"days": [{"date": "2099-01-02"}, {"date": "2099-01-01"}]})
        joined = "\n".join(checks.run_check(self.root, TODAY)[0])
        self.assertIn("bookings.json：items[1].date（a）：'2027-02-30' is not a 'date'", joined)
        bookings = records.record_problems(f"trips/{self.trip.slug}/bookings.json", '{"items": [{"id": "a", "item": "x", "status": "待訂"}, {"id": "a", "item": "y", "status": "已訂"}]}')
        self.assertEqual(bookings, [f"trips/{self.trip.slug}/bookings.json：items[1]：id a 重複"])
        self.assertIn("itinerary.json：days 要依日期排", joined)
        self.assertIn("JSON 格式錯誤", records.record_problems(f"trips/{self.trip.slug}/budget.json", "{")[0])
        readme = (self.trip.path / "README.md").read_text(encoding="utf-8")
        self.assertIn("README.md 開頭：status：'想想' is not one of",
                      records.record_problems(f"trips/{self.trip.slug}/README.md", readme.replace("status: 規劃中", "status: 想想"))[0])
        self.assertEqual(records.record_problems("trips/x/notes.md", "{"), [])

    def place(self, pid: str, **extra) -> dict:
        p = {"id": pid, "name": pid, "region": "範例縣", "location": {"lat": 35.0, "lon": 135.7}, "categories": ["sight"],
             "summary": "摘要", "season": {"tier": 3, "why": "理由"}, "sources": [{"url": "https://example.com"}],
             "as_of": "2026-11-10"}
        return {**p, **extra}

    def write_place(self, data: dict, name: str | None = None):
        (self.trip.path / "places").mkdir(exist_ok=True)
        path = self.trip.path / "places" / f"{name or data['id']}.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_check_validates_places_refs_and_stale_plan_places(self):
        self.write_place(self.place("temple", as_of="2026-10-01"))
        self.write_place(self.place("shrine", sources=[{"url": "research/none.md"}]), name="jinja")
        self.write_record("itinerary.json", {"days": [{"date": "2099-01-01", "places": ["temple", "ghost"]}]})
        problems = "\n".join(checks.run_check(self.root, TODAY)[0])
        self.assertIn("places/jinja.json：id shrine 要和檔名（jinja）一樣", problems)
        self.assertIn("itinerary.json：days[0].places[1]：地點 ghost 不存在（places/ghost.json）", problems)
        (self.trip.path / "places" / "jinja.json").unlink()
        self.write_place(self.place("shrine", sources=[{"url": "research/none.md#a"}]))
        problems = "\n".join(checks.run_check(self.root, TODAY)[0])
        self.assertIn("places/shrine.json：sources[0].url：連到的 research/none.md#a 不存在", problems)
        self.write_place(self.place("shrine", facts=[{"label": "預約", "value": "電話 075-123-4567；2026-11-24 起"}]))
        self.assertIn("shrine.json：電話 075-123-4567 要放在 contact.phone", "\n".join(checks.run_check(self.root, TODAY)[0]))
        self.write_place(self.place("shrine", contact={"phone": "075-123-4567"}, facts=[{"label": "地址", "value": "範例市 1-15-11"}]))
        self.write_record("itinerary.json", {"days": [{"date": "2099-01-01", "places": ["temple"], "stay_place": "shrine"}]})
        views.write_views(self.trip, self.root, TODAY)
        problems, warnings = checks.run_check(self.root, TODAY)
        self.assertEqual(problems, [])
        self.assertIn("itinerary.json 用到的地點查詢超過 30 天（最舊 2026-10-01）：temple，做決定前重查", "\n".join(warnings))

    def test_broken_plan_places_are_reported_not_crashed_on(self):
        self.write_place(self.place("temple", as_of="2026-02-30"))
        (self.trip.path / "places" / "shrine.json").write_text("{", encoding="utf-8")
        self.write_record("itinerary.json", {"days": [{"date": "2099-01-01", "places": ["temple", "shrine"]}]})
        problems = "\n".join(checks.run_check(self.root, TODAY)[0])
        self.assertIn("temple.json：as_of：'2026-02-30' is not a 'date'", problems)
        self.assertIn("shrine.json:1：JSON 格式錯誤", problems)

    def test_place_categories_must_be_defined_in_map_config(self):
        self.write_place(self.place("temple"))
        (self.trip.path / "map").mkdir()
        config = {"title": "t", "short_title": "t", "storage_prefix": "t", "home": "temple",
                  "categories": [{"id": "food", "glyph": "食", "name": "美食"}], "season": {"label": "4 月", "tiers": {
                      str(i): "x" for i in range(1, 6)}}, "drive": {"max_hours": 4}, "basemap": {"wide": [7, 0, 0, 0, 0], "fine": [8, 0, 0, 0, 0]}}
        (self.trip.path / "map" / "config.json").write_text(json.dumps(config), encoding="utf-8")
        problems = checks.run_check(self.root, TODAY)[0]
        # 紀錄有問題時不另外跑地圖頁的 --check（同樣的問題不報兩次）
        self.assertEqual(problems, [f"trips/{self.trip.slug}/places/temple.json：categories[0]：類別 sight 沒有定義在 map/config.json 的 categories"])
        self.assertEqual([g[0] for g in build.generators(self.root)], [f"seasoningway.tripmap.build trips/{self.trip.slug}"])


if __name__ == "__main__":
    unittest.main()
