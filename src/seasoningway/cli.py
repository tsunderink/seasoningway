"""seasoningway 的命令列入口。

每個指令在 COMMANDS 宣告一次：說明、參數、處理函式，以及它要哪一種資料 repo（Needs）。
找根目錄和檢查資料格式都在 main 統一處理，處理函式拿到的 root 已經符合宣告。
"""

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from enum import Enum
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
from .legacy import cmd_from_template, find_template
from .project import cmd_migrate, cmd_new
from .published import cmd_published
from .render import cmd_render
from .save import cmd_save
from .status import cmd_status

Handler = Callable[[argparse.Namespace, Path | None, date], int]


class Needs(Enum):
    """指令要的資料 repo。"""
    NOTHING = "nothing"  # 不需要；有 --root 就傳進去，否則傳 None，由處理函式自己決定
    HERE = "here"  # 沒有 --root 就用目前目錄（建立資料 repo）
    REPO = "repo"  # 從目前目錄往上找 seasoningway.toml
    CURRENT = "current"  # 同 REPO，而且資料格式要和這版引擎相同


@dataclass(frozen=True)
class Command:
    name: str
    help: str
    handler: Handler
    needs: Needs = Needs.REPO
    configure: Callable[[argparse.ArgumentParser], None] = lambda parser: None
    locate: Callable[[argparse.Namespace, Path], Path | None] | None = None  # 取代預設的找法


def configure_new(p: argparse.ArgumentParser) -> None:
    p.add_argument("slug", help="YYYY-MM-英文地名，例如 2099-01-example-city")
    p.add_argument("--title", help="旅程標題")
    p.add_argument("--travelers", type=int, default=1, help="人數，預設 1")
    p.add_argument("--currency", help=f"預算基準幣別，預設讀 {CONFIG} 的 budget.default_currency")


def configure_save(p: argparse.ArgumentParser) -> None:
    p.add_argument("--check", action="store_true", help="檢查驗證、工作區和推送狀態")
    p.add_argument("-m", "--message", help="附在自動產生的 commit 訊息後")


def configure_hook(p: argparse.ArgumentParser) -> None:
    p.add_argument("tool", choices=("claude", "codex", "git"), help="執行 hook 的工具")
    p.add_argument("event", help="hook 事件")
    p.add_argument("hook_args", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)


def configure_published(p: argparse.ArgumentParser) -> None:
    p.add_argument("folder", metavar="旅程", help=f"旅程代號或發布紀錄的資料夾，例如 {TRIPS}/<旅程>/map")
    p.add_argument("version", help="發布後拿到的版本號")
    p.add_argument("--slug", help="artifact 的 slug（第一次發布必填）")


def slug_argument(help_text: str, optional: bool = False) -> Callable[[argparse.ArgumentParser], None]:
    return lambda p: p.add_argument("slug", nargs="?" if optional else None, help=help_text)


def migrate(args: argparse.Namespace, root: Path, today: date) -> int:
    return (cmd_from_template if args.from_template else cmd_migrate)(args, root, today)


def migrate_root(args: argparse.Namespace, cwd: Path) -> Path | None:
    if not args.from_template:
        return find_root(cwd)
    root = find_template(cwd) or find_root(cwd)
    if root is None:
        raise Problem("找不到舊模板 repo 的 data/trips.toml")
    return root


COMMANDS = (
    Command("status", "開場：列出旅程、下一步和要處理的問題，工作區乾淨時快轉同步", cmd_status),
    Command("render", "驗證資料再產生頁面到 out/；驗不過就不產出，逐項列出問題", cmd_render,
            configure=slug_argument("指定旅程代號，預設產生全部", optional=True)),
    Command("check", "驗證資料、引用與個資（CI 用）", cmd_check),
    Command("save", "驗證、commit、依 git.mode 同步；--check 只檢查不保存", cmd_save, configure=configure_save),
    Command("fetch", "抓要連網的快取（路線、底圖、地點 ID），寫進旅程的 cache/", cmd_fetch, Needs.CURRENT,
            configure_fetch),
    Command("new", "從範本開一趟新旅程", cmd_new, Needs.CURRENT, configure_new),
    Command("budget", "計算預算並印出合計", cmd_budget, Needs.CURRENT, slug_argument("旅程代號")),
    Command("published", "發布頁面後記下狀態", cmd_published, Needs.CURRENT, configure_published),
    Command("migrate", "把資料升級到目前引擎的格式", migrate,
            configure=lambda p: p.add_argument("--from-template", action="store_true",
                                               help="從舊模板 repo 搬出 data/ 並初始化"),
            locate=migrate_root),
    Command("guide", "印出流程說明（給讀不到 skill 的 agent）", cmd_guide, Needs.NOTHING,
            lambda p: p.add_argument("topic", choices=TOPICS, help="流程主題")),
    Command("init", "在目前目錄建立資料 repo，寫入各 agent 的轉接檔", cmd_init, Needs.HERE),
    Command("upgrade", "換引擎版本後更新轉接檔並遷移資料", cmd_upgrade),
    Command("hook", "給各工具的 hook 和 git hooks 呼叫：hook <claude|codex|git> <事件>", cmd_hook, Needs.NOTHING,
            configure_hook),
)
BY_NAME = {command.name: command for command in COMMANDS}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="seasoningway", description="讓 AI agent 規劃旅程的引擎")
    parser.add_argument("--version", action="version", version=f"seasoningway {__version__}")
    parser.add_argument("--root", help="資料 repo 的根目錄，預設從目前目錄往上找 seasoningway.toml")
    sub = parser.add_subparsers(dest="command", metavar="<指令>")
    for command in COMMANDS:
        command.configure(sub.add_parser(command.name, help=command.help, description=command.help))
    return parser


def require_current_format(root: Path) -> None:
    if not (root / CONFIG).is_file():
        raise Problem(f"還沒有 {CONFIG}：這個目錄需要已建立的資料 repo")
    version = load_config(root).format
    if not version:
        raise Problem(f"{CONFIG} 還沒初始化：請先完成資料 repo 的設定")
    if version != FORMAT:
        action = "執行 seasoningway migrate" if version < FORMAT else "更新引擎"
        raise Problem(f"{CONFIG} 的資料格式是第 {version} 版：請先{action}")


def resolve_root(command: Command, args: argparse.Namespace, root: Path | None) -> Path | None:
    """依指令的宣告找出資料 repo 的根目錄。"""
    if root is None and command.needs is not Needs.NOTHING:
        cwd = Path.cwd()
        if command.locate:
            root = command.locate(args, cwd)
        else:
            root = cwd if command.needs is Needs.HERE else find_root(cwd)
        if root is None:
            raise Problem(f"找不到 {CONFIG}：請用 --root 指定已建立的資料 repo")
    root = root.resolve() if root is not None else None
    if command.needs is Needs.CURRENT:
        require_current_format(root)
    return root


def main(argv: list[str] | None = None, *, root: Path | None = None, today: date | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    command = BY_NAME[args.command]
    try:
        if args.root is not None:
            root = Path(args.root)
        return command.handler(args, resolve_root(command, args, root), today or date.today())
    except (Problem, OSError, UnicodeError) as error:
        print(f"錯誤：{error}", file=sys.stderr)
        return 1
