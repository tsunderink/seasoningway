"""顯示資料 repo 狀態與待處理事項。"""
import re
from datetime import date
from pathlib import Path

from .budget import PLAN_STATUSES, committed_line, compute_budget, target_line, total
from .checks import print_issues, run_check
from .common import TRIPS, Problem
from .config import config_problems, engine_warning
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
