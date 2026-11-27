"""Claude Code、Codex 與 Git 的輸入和回饋轉接。"""
import io
import json
import os
import re
import sys
from collections import Counter
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory

from .adapters import GIT_EVENTS, adapter_path
from .checks import cmd_check, file_problems
from .common import CONFIG, OUT, TRIPS, Problem
from .config import find_root
from .gitops import checked_git, git_repo_problem, run_git
from .pii import added_pii, scan_text
from .records import missing_dependency, record_problems
from .save import save_check
from .status import cmd_status

EVENTS = ("session-start", "pre-edit", "post-edit", "stop")
AGENT_WRITES = {
    "spot-scout": ("places/*.json",),
    "fact-checker": ("places/*.json", "research/**/*.md", "budget.json", "bookings.json", "itinerary.json"),
    "page-checker": (),  # 腳本和截圖放在 repo 外的暫存目錄
}
PATCH_FILE = re.compile(r"^\*\*\* (Add|Update|Delete) File: (.+)$")
PATCH_MOVE = "*** Move to: "
GIT_OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")


@dataclass
class PatchFile:
    path: str
    action: str
    move_to: str = ""
    removed: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)


def patch_files(patch: str) -> list[PatchFile]:
    files = []
    for line in patch.splitlines():
        if match := PATCH_FILE.fullmatch(line):
            files.append(PatchFile(match[2], match[1]))
        elif files and line.startswith(PATCH_MOVE):
            files[-1].move_to = line.removeprefix(PATCH_MOVE)
        elif files and line.startswith(("+", "-")):
            (files[-1].added if line.startswith("+") else files[-1].removed).append(line[1:])
    return files


def relative(root: Path, path: Path) -> str | None:
    if path.is_relative_to(root):
        name = path.relative_to(root).as_posix()
        return name.lower() if os.name == "nt" else name
    return None


def denied_path(root: Path, path: Path, agent: str) -> str | None:
    # 檢查工具指定的位置和連結實際指到的位置；兩邊都必須符合限制。
    for candidate in (Path(os.path.abspath(path)), path.resolve()):
        rel = relative(root, candidate)
        if rel and adapter_path(rel):
            return f"{rel} 是轉接檔，請用 seasoningway upgrade 更新。"
        if rel and rel.split("/")[0] == OUT:
            return f"{rel} 是產物，請修改來源後執行 seasoningway render。"
        if rel and rel.split("/")[0] == ".git":
            return f"{rel} 是 Git 的內部檔案，請使用 Git 指令。"
        patterns = AGENT_WRITES.get(agent)
        if patterns is not None:
            if rel is None and agent == "page-checker":
                continue
            parts = rel.split("/", 2) if rel else ()
            if len(parts) != 3 or parts[0] != TRIPS or not any(PurePosixPath(parts[2]).full_match(p) for p in patterns):
                where = "、".join(f"{TRIPS}/<旅程>/{p}" for p in patterns) or "repo 外的暫存目錄"
                return f"{agent} 只能寫 {where}。其他改動請交給主 session。"
    return None


def pii_lines(text: str, rel: str) -> Counter:
    lines = text.splitlines()
    return Counter((lines[n - 1], kind) for n, kind in scan_text(text, rel))


def edit_denial(rel: str, before: str, after: str, *, schema: bool) -> str | None:
    kinds = sorted({kind for _, kind in pii_lines(after, rel) - pii_lines(before, rel)})
    if kinds:
        return f"{rel}：這次改動含疑似個資（{'、'.join(kinds)}），請移除或改用角色代稱。"
    if schema and not missing_dependency():
        old = set(record_problems(rel, before)) if before else set()
        new = [problem for problem in record_problems(rel, after) if problem not in old]
        if new:
            return f"{rel}：這次改動不符合紀錄格式。\n" + "\n".join(new[:8])
    return None


def edited_text(path: Path, tool: str, args: dict) -> tuple[str, str]:
    if tool == "NotebookEdit":
        return "", args.get("new_source", "")
    before = path.read_text(encoding="utf-8") if path.is_file() else ""
    if tool == "Write":
        return before, args.get("content", "")
    after = before
    for edit in args.get("edits", []) if tool == "MultiEdit" else [args]:
        after = after.replace(edit["old_string"], edit["new_string"], -1 if edit.get("replace_all") else 1)
    return before, after


def claude_pre_edit(root: Path, base: Path, payload: dict) -> str | None:
    args = payload.get("tool_input", {})
    filename = args.get("file_path") or args.get("notebook_path")
    if not filename:
        return None
    path = base / filename
    if refusal := denied_path(root, path, payload.get("agent_type") or ""):
        return refusal
    if rel := relative(root, path.resolve()):
        tool = payload.get("tool_name", "")
        before, after = edited_text(path, tool, args)
        return edit_denial(rel, before, after, schema=tool != "NotebookEdit")
    return None


def codex_pre_edit(root: Path, base: Path, payload: dict) -> str | None:
    agent = payload.get("agent_type") or ""
    for change in patch_files(payload.get("tool_input", {}).get("command", "")):
        source = base / change.path
        target = base / change.move_to if change.move_to else source
        if refusal := denied_path(root, source, agent) or denied_path(root, target, agent):
            return refusal
        rel = relative(root, target.resolve())
        if not rel or change.action == "Delete":
            continue
        # apply_patch 的 Update 只有差異，完整格式留給 post-edit。Add 有完整檔案內容。
        removed, added = "\n".join(change.removed), "\n".join(change.added)
        if change.move_to and source.is_file():
            moved = source.read_text(encoding="utf-8").splitlines()
            for line in change.removed:
                if line in moved:
                    moved.remove(line)
            removed, added = "", "\n".join(moved + change.added)
        if refusal := edit_denial(rel, removed, added, schema=change.action == "Add"):
            return refusal
    return None


def post_edit(root: Path, base: Path, tool: str, payload: dict) -> list[str]:
    args = payload.get("tool_input", {})
    if tool == "claude":
        files = (args.get("file_path") or args.get("notebook_path"),)
    else:
        files = (change.move_to or change.path for change in patch_files(args.get("command", ""))
                 if change.action != "Delete")
    problems = []
    for filename in dict.fromkeys(files):
        if not filename:
            continue
        path = (base / filename).resolve()
        if path.is_relative_to(root):
            problems += file_problems(root, path)
    return problems


def answer(value: dict) -> int:
    # ASCII JSON 可經 Windows 的 PowerShell 管線而保留中文內容。
    print(json.dumps(value))
    return 0


def block(tool: str, event: str, reason: str) -> int:
    if event == "pre-edit":
        return answer({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason,
        }})
    if tool == "codex":
        return answer({"decision": "block", "reason": reason})
    print(reason, file=sys.stderr)
    return 2


def context(tool: str, text: str) -> int:
    if tool == "codex":
        return answer({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}})
    print(text)
    return 0


def agent_hook(args, root: Path | None, today, payload: dict) -> int:
    tool, event = args.tool, args.event
    if event == "stop" and payload.get("stop_hook_active"):
        return answer({}) if tool == "codex" else 0
    try:
        project = os.environ.get("CLAUDE_PROJECT_DIR") if tool == "claude" else None
        cwd = Path(payload.get("cwd") or project or root or Path.cwd()).resolve()
        root = root or find_root(Path(project) if project else cwd)
        if root is None:
            if event != "session-start":
                raise Problem(f"找不到 {CONFIG}，請從資料 repo 啟動")
            return context(tool, f"找不到 {CONFIG}，可用 seasoningway init 建立資料 repo。")
        if event in ("session-start", "stop"):
            output = io.StringIO()
            with redirect_stdout(output):
                code = cmd_status(args, root, today) if event == "session-start" else save_check(root, today)
            if event == "session-start":
                return context(tool, output.getvalue().rstrip())
            if code:
                return block(tool, event, output.getvalue().rstrip())
            return answer({}) if tool == "codex" else 0
        if event == "pre-edit":
            handler = claude_pre_edit if tool == "claude" else codex_pre_edit
            reason = handler(root, cwd, payload)
        else:
            problems = post_edit(root, cwd, tool, payload)
            reason = "改完的檔案有問題，請修正：\n" + "\n".join(problems) if problems else None
    except (Problem, OSError, UnicodeError, KeyError, TypeError, ValueError) as error:
        reason = f"seasoningway hook {event} 無法完成：{error}"
        if event == "session-start":
            return context(tool, reason)
    return block(tool, event, reason) if reason else 0


def pre_commit(args, root: Path, today) -> int:
    if checked_git(root, "ls-files", "-z", "--", OUT):
        raise Problem(f"不能提交 {OUT}/ 裡的產物")
    with TemporaryDirectory(prefix="seasoningway-index-") as folder:
        # 驗證索引快照，避免未暫存的修正掩蓋即將提交的問題。
        checked_git(root, "checkout-index", "--all", "--ignore-skip-worktree-bits", f"--prefix={Path(folder).as_posix()}/")
        return cmd_check(args, Path(folder), today)


def pre_push(root: Path, remote: str, updates: str) -> int:
    commits = set()
    tracking = f"--remotes={remote}" if remote and not run_git(root, "remote", "get-url", remote).returncode else None
    for line in updates.splitlines():
        fields = line.split()
        if len(fields) != 4 or not all(GIT_OID.fullmatch(fields[i]) for i in (1, 3)):
            raise Problem("Git pre-push 收到無效的 ref 更新")
        ref, local, _, previous = fields
        if not local.strip("0"):  # 刪除遠端 ref
            continue
        old = f"{previous}^{{commit}}"
        known = previous.strip("0") and not run_git(root, "cat-file", "-e", old).returncode
        tip = run_git(root, "rev-parse", "--verify", f"{local}^{{commit}}")
        if tip.returncode:
            if ref.startswith("refs/tags/") and not run_git(root, "cat-file", "-e", local).returncode:
                continue  # 非 commit 的 tag 沒有 commit 可以掃描
            raise Problem(f"無法讀取 {ref} 要推送的 commit")
        exclude = ([tracking] if tracking else []) + ([old] if known else [])
        commits.update(checked_git(root, "rev-list", tip.stdout.strip(), "--not", *exclude).splitlines())
    problems = []
    for sha in sorted(commits):
        message = checked_git(root, "show", "-s", "--format=%B", sha)
        hits = added_pii(root, sha) + [f"commit 訊息第 {n} 行：{kind}" for n, kind in scan_text(message)]
        problems += [f"{sha[:12]} {hit}" for hit in hits]
    for problem in problems:
        print(f"pre-push：疑似個資 {problem}", file=sys.stderr)
    return int(bool(problems))


def cmd_hook(args, root: Path | None, today) -> int:
    events = GIT_EVENTS if args.tool == "git" else EVENTS
    if args.event not in events:
        raise Problem(f"hook {args.tool} 支援的事件：{'、'.join(events)}")
    if args.tool == "git":
        root = root or find_root(Path.cwd())
        if root is None:
            raise Problem(f"找不到 {CONFIG}")
        if error := git_repo_problem(root):
            raise Problem(error)
        if args.event == "pre-commit":
            return pre_commit(args, root, today)
        return pre_push(root, args.hook_args[0] if args.hook_args else "", sys.stdin.read())
    try:
        stream = sys.stdin
        text = stream.buffer.read().decode("utf-8-sig") if hasattr(stream, "buffer") else stream.read()
        payload = json.loads(text) if text.strip() else {}
        if not isinstance(payload, dict):
            raise ValueError("hook payload 需要 JSON 物件")
    except (UnicodeError, ValueError):
        reason = "hook 收到無效的 UTF-8 JSON。"
        return context(args.tool, reason) if args.event == "session-start" else block(args.tool, args.event, reason)
    return agent_hook(args, root, today, payload)
