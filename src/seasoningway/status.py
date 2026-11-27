"""顯示資料 repo 狀態與待處理事項。"""
from datetime import date
from pathlib import Path

from .checks import print_issues, run_check
from .common import Problem
from .config import config_problems, engine_warning
from .gitops import active_operation, checked_git, dirty, git_repo_problem, git_sync
from .save import sync_target
from .views import print_active_trips


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


def cmd_status(args, root: Path, today: date) -> int:
    print("[旅程 repo]")
    cfg, config_errors = config_problems(root)
    notes = []
    if warning := engine_warning(cfg):
        notes.append(warning)
    if not notes and not config_errors:
        try:
            sync_status(root, cfg.git_mode or "local")
        except Problem as error:
            notes.append(str(error))
    print_active_trips(root)
    problems, warnings = run_check(root, today)
    print_issues(problems, notes + warnings)
    return int(bool(problems))
