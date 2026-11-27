"""seasoningway 的命令列入口。

介面在 docs/design.md「指令」定案；還沒實作的指令會指出 docs/migration.md 的哪一階段負責。
"""

import argparse
import sys
from datetime import date
from pathlib import Path

from . import __version__
from .budget import cmd_budget
from .build import cmd_build
from .checks import cmd_check
from .common import CONFIG, TRIPS, Problem
from .config import find_root, load_config
from .project import cmd_migrate, cmd_new
from .published import cmd_published

# (名稱, 說明, 負責實作的 migration.md 階段)
COMMANDS = [
    ("status", "開場：列出旅程、下一步和要處理的問題，工作區乾淨時快轉同步", 2),
    ("render", "驗證資料再產生頁面到 out/；驗不過就不產出，逐項列出問題", 2),
    ("check", "驗證資料、引用、個資與產物是否最新（CI 用）", 1),
    ("save", "驗證、commit、依 git.mode 同步；--check 只檢查不保存", 2),
    ("fetch", "抓要連網的快取（路線、底圖、地點 ID），寫進旅程的 cache/", 2),
    ("new", "從範本開一趟新旅程", 1),
    ("budget", "計算預算並印出合計", 1),
    ("published", "發布頁面後記下狀態", 1),
    ("migrate", "把資料升級到目前引擎的格式", 1),
    ("build", "更新旅程總覽、預算、預訂、行程和地圖（第 1 階段沿用）", 1),
    ("guide", "印出流程說明（給讀不到 skill 的 agent）", 3),
    ("init", "在目前目錄建立資料 repo，寫入各 agent 的轉接檔", 3),
    ("upgrade", "換引擎版本後更新轉接檔並遷移資料", 3),
    ("hook", "給各工具的 hook 和 git hooks 呼叫：hook <claude|codex|git> <事件>", 3),
]

HANDLERS = {
    "budget": cmd_budget,
    "build": cmd_build,
    "check": cmd_check,
    "migrate": cmd_migrate,
    "new": cmd_new,
    "published": cmd_published,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="seasoningway", description="讓 AI agent 規劃旅程的引擎")
    parser.add_argument("--version", action="version", version=f"seasoningway {__version__}")
    parser.add_argument("--root", help="資料 repo 的根目錄，預設從目前目錄往上找 seasoningway.toml")
    sub = parser.add_subparsers(dest="command", metavar="<指令>")
    for name, help_text, _ in COMMANDS:
        p = sub.add_parser(name, help=help_text, description=help_text)
        if name == "new":
            p.add_argument("slug", help="YYYY-MM-英文地名，例如 2099-01-example-city")
            p.add_argument("--title", help="旅程標題")
            p.add_argument("--travelers", type=int, default=1, help="人數，預設 1")
            p.add_argument("--currency", help=f"預算基準幣別，預設讀 {CONFIG} 的 budget.default_currency")
        elif name == "budget":
            p.add_argument("slug", help="旅程代號")
        elif name == "published":
            p.add_argument("folder", help=f"artifact 的資料夾，例如 {TRIPS}/<旅程>/map")
            p.add_argument("version", help="發布後拿到的版本號")
            p.add_argument("--slug", help="artifact 的 slug（第一次發布必填）")
            p.add_argument("--include", nargs="+", metavar="樣式", help="要發布的檔案，相對資料夾的 glob")
        elif name not in HANDLERS:
            p.add_argument("args", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None, *, root: Path | None = None, today: date | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = build_parser()
    ns = parser.parse_args(argv)
    if ns.command is None:
        parser.print_help()
        return 0
    handler = HANDLERS.get(ns.command)
    if handler is None:
        stage = {name: stage for name, _, stage in COMMANDS}[ns.command]
        print(f"seasoningway {ns.command}：尚未實作，見 docs/migration.md 第 {stage} 階段", file=sys.stderr)
        return 2
    try:
        if ns.root is not None:
            root = Path(ns.root).resolve()
        elif root is None:
            root = find_root(Path.cwd())
            if root is None and ns.command == "migrate":
                root = Path.cwd()
        if root is None:
            raise Problem(f"找不到 {CONFIG}：指定 --root，或執行 seasoningway migrate 初始化")
        if ns.command in ("new", "budget", "build", "published"):
            if not (root / CONFIG).is_file():
                raise Problem(f"還沒有 {CONFIG}：先執行 seasoningway migrate 初始化")
            if load_config(root, validate=False).format == 0:
                raise Problem(f"{CONFIG} 還沒初始化：先執行 seasoningway migrate")
        return handler(ns, root, today or date.today())
    except Problem as error:
        print(f"錯誤：{error}", file=sys.stderr)
        return 1
