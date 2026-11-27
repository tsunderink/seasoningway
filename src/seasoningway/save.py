"""驗證並依資料 repo 的 Git 設定保存。"""
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .checks import print_issues, run_check
from .common import CONFIG, OUT, TRIPS, Problem
from .config import config_problems, engine_warning
from .files import write_text
from .gitops import (active_operation, checked_git, dirty, git_path, git_repo_problem,
                     git_sync, run_git, unmerged)
from .pii import scan_text


@dataclass
class Target:
    remote: str
    ref: str
    tracking: str


def sync_target(root: Path, mode: str) -> Target | None:
    if mode == "local":
        return None
    branch = checked_git(root, "branch", "--show-current").strip()
    if mode == "main":
        if branch != "main":
            raise Problem("git.mode = main：請先切到 main")
        checked_git(root, "remote", "get-url", "origin")
        return Target("origin", "refs/heads/main", "refs/remotes/origin/main")
    if not branch:
        raise Problem("git.mode = branch：目前是 detached HEAD，請先切到要保存的分支")
    tracking = checked_git(root, "for-each-ref", "--format=%(upstream)%00%(upstream:remotename)%00%(upstream:remoteref)",
                           f"refs/heads/{branch}").strip().split("\0")
    if len(tracking) != 3 or not all(tracking):
        raise Problem(f"{branch} 還沒有 upstream：請先設定這個分支的 upstream")
    return Target(tracking[1], tracking[2], tracking[0])


def unpushed_count(root: Path, target: Target) -> int:
    if run_git(root, "rev-parse", "--verify", "--quiet", "HEAD").returncode:
        return 0
    has_tracking = not run_git(root, "show-ref", "--verify", "--quiet", target.tracking).returncode
    exclude = ["--not", target.tracking] if has_tracking else []
    return int(checked_git(root, "rev-list", "--count", "HEAD", *exclude).strip())


def names(text: str) -> set[str]:
    return set(filter(None, text.split("\0")))


def data_path(path: str) -> bool:
    return path == CONFIG or path.startswith((f"{TRIPS}/", "profile/"))


def new_paths(root: Path) -> set[str]:
    untracked = names(checked_git(root, "ls-files", "-z", "--others", "--exclude-standard"))
    added = names(checked_git(root, "diff", "--cached", "--name-only", "--no-renames", "--diff-filter=A", "-z"))
    return untracked | added


def check_paths(root: Path) -> set[str]:
    if checked_git(root, "ls-files", "-z", "--", OUT):
        raise Problem(f"{OUT}/ 有已追蹤的產物：請先從 Git 索引移除並保留本地檔案")
    paths = new_paths(root)
    outside = sorted(path for path in paths if not data_path(path))
    if outside:
        raise Problem("資料路徑外有新檔，請移到資料路徑內或加到 .gitignore：\n" + "\n".join(outside))
    return paths


def commit_message(paths: set[str], note: str | None) -> str:
    trips = sorted({p.split("/")[1] for p in paths if p.startswith(f"{TRIPS}/")})
    kinds = set()
    for path in paths:
        parts = path.split("/")
        if path == CONFIG:
            kinds.add("設定")
        elif parts[0] == "profile":
            kinds.add("偏好與心得")
        elif parts[0] == TRIPS:
            name = parts[2].lower() if len(parts) > 2 else ""
            kinds.add({
                "readme.md": "旅程說明", "budget.json": "預算", "bookings.json": "預訂",
                "itinerary.json": "行程", "places": "地點", "research": "研究",
                "cache": "快取", "map": "地圖", "published.json": "發布紀錄",
            }.get(name, "旅程檔案"))
        else:
            kinds.add("其他已追蹤檔案")
    subject = "保存" + (" " + "、".join(trips) if trips else "旅程資料") + "（" + "、".join(sorted(kinds)) + "）"
    return subject + (f"\n\n{note.strip()}" if note and note.strip() else "")


def commit_changes(root: Path, paths: set[str], note: str | None) -> bool:
    checked_git(root, "add", "-u", "--", ".")
    existing = sorted(path for path in paths if (root / path).exists() or (root / path).is_symlink())
    if existing:
        checked_git(root, "--literal-pathspecs", "add", "--", *existing)
    staged = names(checked_git(root, "diff", "--cached", "--name-only", "--no-renames", "-z"))
    if not staged:
        return False
    checked_git(root, "commit", "-m", commit_message(staged, note))
    return True


def verify_data(root: Path, today: date) -> None:
    problems, warnings = run_check(root, today)
    mismatch = engine_warning(config_problems(root)[0])
    if mismatch:
        problems.append(mismatch)
    print_issues(problems, warnings)
    if problems:
        raise Problem("資料驗證未通過")


def sync_failure(root: Path, operation: str, error: Problem) -> Problem:
    pending = unmerged(root)
    detail = "\n".join(pending) if pending else str(error)
    return Problem(f"{operation} 尚未完成，整合衝突並確認驗證通過後再執行 seasoningway save\n{detail}")


def continue_sync(root: Path, operation: str, marker: Path) -> None:
    kind = "rebase" if operation.startswith("rebase") else operation
    if kind not in ("rebase", "merge") or not marker.is_file() or marker.read_text(encoding="utf-8").strip() != kind:
        raise Problem(f"Git 正在執行 {operation}：請先處理這個操作")
    pending = unmerged(root)
    if pending:
        checked_git(root, "--literal-pathspecs", "add", "-A", "--", *pending)
    try:
        checked_git(root, kind, "--continue")
    except Problem as error:
        if active_operation(root):
            raise sync_failure(root, kind, error) from None
        raise
    marker.unlink(missing_ok=True)


def pull_main(root: Path, target: Target, marker: Path) -> None:
    git_sync(root, "fetch", "--prune", target.remote)
    if run_git(root, "show-ref", "--verify", "--quiet", target.tracking).returncode:
        return  # 遠端還沒有 main，直接建立
    merges = checked_git(root, "rev-list", "--merges", f"{target.tracking}..HEAD")
    kind = "merge" if merges.strip() else "rebase"
    write_text(marker, kind + "\n")
    try:
        flags = ["--no-rebase", "--ff", "--no-edit"] if kind == "merge" else ["--rebase"]
        git_sync(root, "pull", *flags, target.remote, "main")
    except Problem as error:
        if active_operation(root):
            raise sync_failure(root, kind, error) from None
        raise
    finally:
        if not active_operation(root):
            marker.unlink(missing_ok=True)


def save_check(root: Path, today: date) -> int:
    problems, warnings = run_check(root, today)
    cfg, config_errors = config_problems(root)
    if mismatch := engine_warning(cfg):
        problems.append(mismatch)
    if error := git_repo_problem(root):
        problems.append(error)
    else:
        if operation := active_operation(root):
            problems.append(f"Git 正在執行 {operation}")
        if dirty(root):
            problems.append("有未保存的改動或新檔案")
        if not operation and not config_errors:
            try:
                target = sync_target(root, cfg.git_mode or "local")
                if target and (count := unpushed_count(root, target)):
                    problems.append(f"有 {count} 個 commit 還沒推到 {target.remote}/{target.ref.removeprefix('refs/heads/')}")
            except Problem as error:
                problems.append(str(error))
    print_issues(problems, warnings)
    if not problems:
        print("save --check 通過")
    return int(bool(problems))


def cmd_save(args, root: Path, today: date) -> int:
    if args.check:
        return save_check(root, today)
    if args.message and scan_text(args.message):
        raise Problem("commit 附註含疑似個資，請移除")
    verify_data(root, today)
    if error := git_repo_problem(root):
        raise Problem(error)

    cfg, _ = config_problems(root)
    mode = cfg.git_mode or "local"
    marker = git_path(root, "seasoningway-sync")
    paths = check_paths(root)
    if operation := active_operation(root):
        if mode != "main":
            raise Problem(f"Git 正在執行 {operation}：請先處理這個操作")
        continue_sync(root, operation, marker)
        verify_data(root, today)
        paths = check_paths(root)
    else:
        marker.unlink(missing_ok=True)
    target = sync_target(root, mode)
    if (root / ".githooks").is_dir():
        checked_git(root, "config", "--local", "core.hooksPath", ".githooks")
    committed = commit_changes(root, paths, args.message)

    if target and not run_git(root, "rev-parse", "--verify", "--quiet", "HEAD").returncode:
        if mode == "main":
            pull_main(root, target, marker)
            verify_data(root, today)
        if unpushed_count(root, target):
            git_sync(root, "push", target.remote, f"HEAD:{target.ref}")
    print("已保存旅程資料" if committed or target else "沒有需要保存的改動")
    return 0
