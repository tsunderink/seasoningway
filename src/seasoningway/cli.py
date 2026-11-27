"""seasoningway 的命令列入口。

介面在 docs/design.md「指令」定案；還沒實作的指令會指出 docs/migration.md 的哪一階段負責。
"""

import argparse
import sys

from . import __version__

# (名稱, 說明, 負責實作的 migration.md 階段)
COMMANDS = [
    ("status", "開場：列出旅程、下一步和要處理的問題，工作區乾淨時快轉同步", 2),
    ("render", "驗證資料再產生頁面到 out/；驗不過就不產出，逐項列出問題", 2),
    ("check", "只驗證、不產生頁面（CI 用）", 1),
    ("save", "驗證、commit、依 git.mode 同步；--check 只檢查不保存", 2),
    ("fetch", "抓要連網的快取（路線、底圖、地點 ID），寫進旅程的 cache/", 2),
    ("new", "從範本開一趟新旅程", 1),
    ("budget", "計算預算並印出合計", 1),
    ("published", "發布頁面後記下狀態", 1),
    ("migrate", "把資料升級到目前引擎的格式", 1),
    ("guide", "印出流程說明（給讀不到 skill 的 agent）", 3),
    ("init", "在目前目錄建立資料 repo，寫入各 agent 的轉接檔", 3),
    ("upgrade", "換引擎版本後更新轉接檔並遷移資料", 3),
    ("hook", "給各工具的 hook 和 git hooks 呼叫：hook <claude|codex|git> <事件>", 3),
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="seasoningway", description="讓 AI agent 規劃旅程的引擎")
    parser.add_argument("--version", action="version", version=f"seasoningway {__version__}")
    parser.add_argument("--root", help="資料 repo 的根目錄，預設從目前目錄往上找 seasoningway.toml")
    sub = parser.add_subparsers(dest="command", metavar="<指令>")
    for name, help_text, _ in COMMANDS:
        p = sub.add_parser(name, help=help_text, description=help_text)
        p.add_argument("args", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    ns = parser.parse_args(argv)
    if ns.command is None:
        parser.print_help()
        return 0
    stage = {name: stage for name, _, stage in COMMANDS}[ns.command]
    print(f"seasoningway {ns.command}：尚未實作，見 docs/migration.md 第 {stage} 階段", file=sys.stderr)
    return 2
