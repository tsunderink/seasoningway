"""讀取 Git 狀態、執行同步與掃描檔案。"""
import os
import re
import subprocess
import time
from pathlib import Path

from .common import OUT, Problem


def files_to_scan(root: Path) -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=root, capture_output=True, check=True,
        ).stdout.decode("utf-8")
        names = [n for n in out.split("\0") if n]
    except (OSError, subprocess.CalledProcessError):
        names = [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and ".git" not in p.parts]
    names = sorted(set(names))
    return [root / n for n in names if n.split("/")[0] != OUT and (root / n).is_file()]


HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)")
GIT_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}


def diff_path(header: str) -> str | None:
    """diff 的「+++ b/路徑」標頭裡的路徑。含空白的路徑後面多一個 tab；含引號、反斜線、控制字元的
    整個加上引號、用 C 的跳脫寫法。刪掉的檔（+++ /dev/null）回傳 None。"""
    name = header[4:].removesuffix("\t")
    if name.startswith('"') and name.endswith('"'):
        out, body, i = bytearray(), name[1:-1], 0
        while i < len(body):
            if body[i] == "\\" and body[i + 1:i + 2] in GIT_ESCAPES:
                out.append(GIT_ESCAPES[body[i + 1]])
                i += 2
            elif body[i] == "\\":  # 八進位的位元組，例如 \303
                out.append(int(body[i + 1:i + 4], 8))
                i += 4
            else:
                out += body[i].encode("utf-8")
                i += 1
        name = out.decode("utf-8", "replace")
    return name[2:] if name.startswith("b/") else None


def added_lines(root: Path, sha: str) -> dict[str, list[tuple[int, str]]]:
    """commit 新增的文字，按路徑分組，附上行號。"""
    diff = subprocess.run(["git", "-c", "core.quotePath=false", "diff-tree", "-r", "-p", "-U0", "--no-renames",
                           "--no-commit-id", "--no-color", sha], cwd=root, capture_output=True).stdout.decode("utf-8", "replace")
    added: dict[str, list[tuple[int, str]]] = {}
    path, header, n = None, False, 0
    for line in diff.split("\n"):
        if line.startswith("diff --git "):
            path, header = None, True
        elif header and line.startswith("+++ "):
            path = diff_path(line)
        elif m := HUNK_RE.match(line):
            header, n = False, int(m[1])
        elif not header and path and line.startswith("+"):
            added.setdefault(path, []).append((n, line[1:]))
            n += 1
    return added


def run_git(root: Path, *args: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never",
           "GIT_OPTIONAL_LOCKS": "0", "GIT_EDITOR": "true"}
    try:
        return subprocess.run(["git", *args], cwd=root, env=env, capture_output=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        return subprocess.CompletedProcess(args, 1, "", str(error))


def git_error(args: tuple[str, ...], result: subprocess.CompletedProcess[str]) -> Problem:
    detail = (result.stderr or result.stdout).strip() or f"結束碼 {result.returncode}"
    return Problem(f"git {args[0]} 失敗：{detail}")


def checked_git(root: Path, *args: str) -> str:
    result = run_git(root, *args)
    if result.returncode:
        raise git_error(args, result)
    return result.stdout


NETWORK_ERRORS = ("could not resolve", "failed to connect", "connection refused", "connection reset",
                  "connection timed out", "operation timed out", "timed out after", "remote end hung up",
                  "network is unreachable", "temporary failure", "http 502", "http 503", "http 504")


def git_sync(root: Path, *args: str) -> None:
    for attempt in range(5):
        result = run_git(root, *args)
        if not result.returncode:
            return
        detail = (result.stderr + result.stdout).lower()
        if attempt == 4 or not any(error in detail for error in NETWORK_ERRORS):
            raise git_error(args, result)
        time.sleep(2 ** attempt)


def git_repo_problem(root: Path) -> str | None:
    result = run_git(root, "rev-parse", "--show-toplevel")
    if result.returncode or Path(result.stdout.strip()).resolve() != root.resolve():
        return "資料根目錄必須是 Git 工作區的根目錄"
    return None


def git_path(root: Path, name: str) -> Path:
    path = checked_git(root, "rev-parse", "--git-path", name).strip()
    return root / path


def active_operation(root: Path) -> str | None:
    for path, kind in (("rebase-merge", "rebase"), ("rebase-apply", "rebase"),
                       ("MERGE_HEAD", "merge"), ("CHERRY_PICK_HEAD", "cherry-pick"),
                       ("REVERT_HEAD", "revert")):
        if git_path(root, path).exists():
            return kind
    return None


def dirty(root: Path) -> bool:
    return bool(checked_git(root, "status", "--porcelain", "-z", "--untracked-files=all"))


def unmerged(root: Path) -> list[str]:
    return sorted(filter(None, checked_git(root, "diff", "--name-only", "--diff-filter=U", "-z").split("\0")))
