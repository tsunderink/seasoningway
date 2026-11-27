"""顯示資料 repo 狀態與待處理事項。"""
import argparse
import json
import re
from datetime import date
from importlib import metadata
from pathlib import Path

from . import __version__
from .adapters import upgrade_repo
from .budget import PLAN_STATUSES, committed_line, compute_budget, target_line, total
from .checks import print_issues, run_check
from .common import TRIPS, Problem
from .config import FORMAT, Config, config_problems, engine_warning, load_config
from .save import cmd_save
from .gitops import active_operation, checked_git, dirty, git_repo_problem, git_sync, sync_target
from .trips import load_trips, section_lines


def print_active_trips(root: Path) -> int:
    active = [t for t in load_trips(root) if t.active]
    if not active:
        print("目前沒有進行中的旅程。")
    for t in active:
        print(f"- {t.slug}｜{t.title}｜{t.status}｜{t.dates()}")
        try:
            if t.budget_target is not None and (t.path / "budget.json").exists():
                b = compute_budget(t, root)
                print("    " + target_line(t, total(b.lines, None, PLAN_STATUSES)) + "；" + committed_line(b))
        except Problem as e:
            print(f"    預算算不出來：{e}")
        steps = section_lines(t.path / "README.md", "下一步")
        for line in steps[:LIST_STEPS]:
            print(f"    下一步 {brief(line)}")
        if len(steps) > LIST_STEPS:
            print(f"    ……還有 {len(steps) - LIST_STEPS} 項")
        if steps:
            print(f"    （每項只列開頭，全文在 {TRIPS}/{t.slug}/README.md）")
    return 0


LIST_STEPS = 8
BRIEF_CHARS = 70


def brief(line: str, limit: int = BRIEF_CHARS) -> str:
    """SessionStart hook 每次開 session、壓縮對話都會印 list，所以每項只留開頭，省 context。"""
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line.strip().removeprefix("- "))
    return text if len(text) <= limit else text[: limit - 1] + "…"


def sync_status(root: Path, mode: str) -> None:
    if error := git_repo_problem(root):
        raise Problem(error)
    if operation := active_operation(root):
        raise Problem(f"Git 正在執行 {operation}，請先結束這個操作")
    if (root / ".githooks").is_dir():
        checked_git(root, "config", "--local", "core.hooksPath", ".githooks")
    if dirty(root):
        raise Problem("工作區有未保存的改動，請先處理")
    target = sync_target(root, mode)
    if target:
        git_sync(root, "pull", "--ff-only", target.remote, target.ref)


def version_key(text: str) -> tuple[int, ...] | None:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", text)
    return tuple(map(int, match.groups())) if match else None


def editable_install() -> bool:
    """從原始碼或可編輯安裝執行時，版本號可能還沒發布，不自動升級。"""
    try:
        text = metadata.distribution("seasoningway").read_text("direct_url.json")
    except metadata.PackageNotFoundError:
        return True
    try:
        info = json.loads(text or "{}")
    except ValueError:
        return False
    return isinstance(info, dict) and isinstance(info.get("dir_info"), dict) and bool(info["dir_info"].get("editable"))


def upgrade_due(cfg: Config) -> bool:
    """安裝的引擎是已發布、且比 engine 新的版本。"""
    pinned, installed = version_key(cfg.engine), version_key(__version__)
    return bool(pinned and installed and installed > pinned) and not editable_install()


def auto_upgrade(args, root: Path, today: date) -> str | None:
    cfg = load_config(root, validate=False)  # 剛快轉同步過，可能已由別處升級
    if not upgrade_due(cfg):
        return engine_warning(cfg)
    upgrade_repo(args, root, today)
    try:
        cmd_save(argparse.Namespace(check=False, message=f"升級引擎到 {__version__}"), root, today)
    except Problem as error:
        raise Problem(f"已自動升級到引擎 {__version__}，但保存失敗：{error}\n修正後執行 seasoningway save") from error
    return f"已自動把資料 repo 從引擎 {cfg.engine} 升級到 {__version__} 並保存"


def cmd_status(args, root: Path, today: date) -> int:
    print("[旅程 repo]")
    cfg, config_errors = config_problems(root)
    notes = []
    # 格式較舊只是還沒 migrate，upgrade 會一起處理；其他設定問題先修
    upgrade = upgrade_due(cfg) and (not config_errors or 0 < cfg.format < FORMAT)
    if not upgrade and (warning := engine_warning(cfg)):
        notes.append(warning)
    elif upgrade or not config_errors:
        try:
            sync_status(root, cfg.git_mode or "local")
        except Problem as error:
            notes.append(f"還沒自動升級到引擎 {__version__}：{error}" if upgrade else str(error))
        else:
            if upgrade:
                try:
                    notes.append(auto_upgrade(args, root, today))
                except Problem as error:
                    notes.append(str(error))
    notes = [note for note in notes if note]
    print_active_trips(root)
    problems, warnings = run_check(root, today)
    print_issues(problems, notes + warnings)
    return int(bool(problems))
