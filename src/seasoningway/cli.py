"""seasoningway 的命令列入口。"""

import argparse
import sys
from datetime import date
from pathlib import Path

from . import __version__
from .adapters import cmd_init, cmd_upgrade
from .budget import cmd_budget
from .checks import cmd_check
from .common import CONFIG, TRIPS, Problem
from .config import FORMAT, find_root, load_config
from .fetch import cmd_fetch, configure_fetch
from .guides import TOPICS, cmd_guide
from .hooks import cmd_hook
from .project import cmd_migrate, cmd_new
from .published import cmd_published
from .render import cmd_render
from .save import cmd_save
from .status import cmd_status

COMMANDS = [
    ("status", "開場：列出旅程、下一步和要處理的問題，工作區乾淨時快轉同步"),
    ("render", "驗證資料再產生頁面到 out/；驗不過就不產出，逐項列出問題"),
    ("check", "驗證資料、引用與個資（CI 用）"),
    ("save", "驗證、commit、依 git.mode 同步；--check 只檢查不保存"),
    ("fetch", "抓要連網的快取（路線、底圖、地點 ID），寫進旅程的 cache/"),
    ("new", "從範本開一趟新旅程"),
    ("budget", "計算預算並印出合計"),
    ("published", "發布頁面後記下狀態"),
    ("migrate", "把資料升級到目前引擎的格式"),
    ("guide", "印出流程說明（給讀不到 skill 的 agent）"),
    ("init", "在目前目錄建立資料 repo，寫入各 agent 的轉接檔"),
    ("upgrade", "換引擎版本後更新轉接檔並遷移資料"),
    ("hook", "給各工具的 hook 和 git hooks 呼叫：hook <claude|codex|git> <事件>"),
]

HANDLERS = {
    "budget": cmd_budget,
    "check": cmd_check,
    "migrate": cmd_migrate,
    "new": cmd_new,
    "published": cmd_published,
    "fetch": cmd_fetch,
    "guide": cmd_guide,
    "hook": cmd_hook,
    "init": cmd_init,
    "render": cmd_render,
    "save": cmd_save,
    "status": cmd_status,
    "upgrade": cmd_upgrade,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="seasoningway", description="讓 AI agent 規劃旅程的引擎")
    parser.add_argument("--version", action="version", version=f"seasoningway {__version__}")
    parser.add_argument("--root", help="資料 repo 的根目錄，預設從目前目錄往上找 seasoningway.toml")
    sub = parser.add_subparsers(dest="command", metavar="<指令>")
    for name, help_text in COMMANDS:
        p = sub.add_parser(name, help=help_text, description=help_text)
        if name == "new":
            p.add_argument("slug", help="YYYY-MM-英文地名，例如 2099-01-example-city")
            p.add_argument("--title", help="旅程標題")
            p.add_argument("--travelers", type=int, default=1, help="人數，預設 1")
            p.add_argument("--currency", help=f"預算基準幣別，預設讀 {CONFIG} 的 budget.default_currency")
        elif name == "budget":
            p.add_argument("slug", help="旅程代號")
        elif name == "render":
            p.add_argument("slug", nargs="?", help="指定旅程代號，預設產生全部")
        elif name == "save":
            p.add_argument("--check", action="store_true", help="檢查驗證、工作區和推送狀態")
            p.add_argument("-m", "--message", help="附在自動產生的 commit 訊息後")
        elif name == "fetch":
            configure_fetch(p)
        elif name == "guide":
            p.add_argument("topic", choices=TOPICS, help="流程主題")
        elif name == "hook":
            p.add_argument("tool", choices=("claude", "codex", "git"), help="執行 hook 的工具")
            p.add_argument("event", help="hook 事件")
            p.add_argument("hook_args", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
        elif name == "published":
            p.add_argument("folder", metavar="旅程", help=f"旅程代號或發布紀錄的資料夾，例如 {TRIPS}/<旅程>/map")
            p.add_argument("version", help="發布後拿到的版本號")
            p.add_argument("--slug", help="artifact 的 slug（第一次發布必填）")
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
    handler = HANDLERS[ns.command]
    try:
        if ns.root is not None:
            root = Path(ns.root).resolve()
        if ns.command in ("guide", "hook"):
            return handler(ns, root.resolve() if root is not None else None, today or date.today())
        if root is None:
            root = Path.cwd() if ns.command == "init" else find_root(Path.cwd())
        if root is None:
            raise Problem(f"找不到 {CONFIG}：請用 --root 指定已建立的資料 repo")
        root = root.resolve()
        if ns.command in ("new", "budget", "published", "fetch"):
            if not (root / CONFIG).is_file():
                raise Problem(f"還沒有 {CONFIG}：這個目錄需要已建立的資料 repo")
            version = load_config(root).format
            if not version:
                raise Problem(f"{CONFIG} 還沒初始化：請先完成資料 repo 的設定")
            if version != FORMAT:
                action = "執行 seasoningway migrate" if version < FORMAT else "更新引擎"
                raise Problem(f"{CONFIG} 的資料格式是第 {version} 版：請先{action}")
        return handler(ns, root, today or date.today())
    except (Problem, OSError, UnicodeError) as error:
        print(f"錯誤：{error}", file=sys.stderr)
        return 1
