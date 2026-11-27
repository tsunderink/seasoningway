"""建立資料 repo，並從同一份流程產生各工具的轉接檔。"""
import errno
import json
import os
import re
import tomllib
from pathlib import Path
from tempfile import TemporaryDirectory

from . import __version__, config
from .common import CONFIG, TRIPS, Problem
from .files import replace_paths
from .gitops import checked_git, git_repo_problem, run_git
from .guides import TOPICS, guide
from .project import OLDEST, TEMPLATES, cmd_migrate, template_files

GENERATED = "由 seasoningway 產生，不要手改"
HOOK_FILES = {"claude": ".claude/settings.json", "codex": ".codex/hooks.json"}
GIT_EVENTS = ("pre-commit", "pre-push")
AGENTS = {
    "spot-scout": (
        "蒐集一個地區的候選景點和餐廳，每個地點寫成一份地點檔。",
        "WebSearch, WebFetch, Read, Grep, Glob, Write, Edit",
        "scout",
        "依主 session 指定的地區，照下方「蒐集一個地區」和「回報」執行。"
        "有 hook 時，依回饋修正寫入問題。",
    ),
    "fact-checker": (
        "重查筆記和資料檔裡過期或待確認的事實，更新內容和查詢日期。",
        "WebSearch, WebFetch, Read, Grep, Glob, Edit",
        "recheck",
        "依主 session 指定的範圍，照下方「重查做法」「分工」「回報」執行。"
        "有 hook 時，依回饋修正寫入問題。",
    ),
    "page-checker": (
        "在手機、平板和桌面尺寸操作產生的網頁，截圖並回報。",
        "Bash, Read, Write, Glob, Grep",
        "check-page",
        "執行 seasoningway guide check-page，照其中的「檢查做法」「分工」「回報」執行。"
        "腳本與截圖放在 repo 外的暫存目錄。",
    ),
}
SKILL_DIRS = (".agents/skills", ".claude/skills")
ADAPTER_DIRS = (".claude/agents", ".codex/agents", ".githooks", *SKILL_DIRS)
ADAPTER_FILES = ("AGENTS.md", "CLAUDE.md", ".github/workflows/check.yml", *HOOK_FILES.values())
ENGINE_LINE = re.compile(
    r"""(?m)^([ \t]*(?:engine|"engine"|'engine')[ \t]*=[ \t]*)(?:"(?:\\.|[^"\\\n])*"|'[^'\n]*')(?=[ \t]*(?:#|$))"""
)


def hook_settings(tool: str) -> dict:
    matcher = "Write|Edit|MultiEdit|NotebookEdit" if tool == "claude" else "apply_patch"
    hooks = {}
    for name, event, pattern, timeout in (
        ("SessionStart", "session-start", "startup|resume|clear|compact", 120),
        ("PreToolUse", "pre-edit", matcher, 30),
        ("PostToolUse", "post-edit", matcher, 120),
        ("Stop", "stop", None, 60),
    ):
        command = {"type": "command", "command": f"seasoningway hook {tool} {event}", "timeout": timeout}
        entry = {"hooks": [command]}
        if pattern and (tool == "codex" or event != "session-start"):
            entry["matcher"] = pattern
        if tool == "claude" and event == "session-start":
            command["statusMessage"] = GENERATED
        hooks[name] = [entry]
    if tool == "codex":
        return {"description": GENERATED, "hooks": hooks}
    return {"permissions": {
        "allow": ["Bash(seasoningway:*)"],
        "deny": [
            "mcp__claude-in-chrome__*",
            "mcp__Claude_Browser__*",
            "mcp__computer-use__*",
            "mcp__remote-devices__Claude_Browser*",
            "mcp__remote-devices__computer*",
        ],
    }, "hooks": hooks}


def template(resource: str, **values: str) -> str:
    text = TEMPLATES.joinpath("adapters", resource).read_text(encoding="utf-8")
    fields = {"generated": GENERATED, **values}
    return re.sub(r"\{\{(\w+)\}\}", lambda match: fields[match[1]], text)


def adapter_files(engine: str) -> dict[str, str]:
    topics = "\n".join(f"- {topic}：{description}" for topic, description in TOPICS.items())
    result = {
        "AGENTS.md": template("AGENTS.md", topics=topics),
        "CLAUDE.md": template("CLAUDE.md"),
        ".github/workflows/check.yml": template("check.yml", engine=engine),
    }
    for tool, path in HOOK_FILES.items():
        result[path] = json.dumps(hook_settings(tool), ensure_ascii=False, indent=2) + "\n"
    for event in GIT_EVENTS:
        result[f".githooks/{event}"] = template("git-hook.sh", event=event)
    for name, description in TOPICS.items():
        for folder in SKILL_DIRS:
            result[f"{folder}/{name}/SKILL.md"] = template("skill.md", name=name, description=description)
    for name, (description, tools, topic, instructions) in AGENTS.items():
        no_shell = name != "page-checker"
        if no_shell:
            instructions += "\n\n" + guide(topic).rstrip()
        result[f".claude/agents/{name}.md"] = template(
            "claude-agent.md", name=name, description=description, tools=tools, instructions=instructions,
        )
        fields = {"name": name, "description": description, "developer_instructions": instructions}
        if no_shell:
            fields["web_search"] = "live"
        toml = "\n".join(f"{key} = {json.dumps(value, ensure_ascii=False)}" for key, value in fields.items())
        features = "\n[features]\nshell_tool = false\n" if no_shell else ""
        result[f".codex/agents/{name}.toml"] = f"# {GENERATED}\n{toml}\n{features}"
    return result


def adapter_path(rel: str) -> bool:
    """這些路徑由 init、upgrade 維護，也保護資料 repo 過去產生的同類轉接檔。"""
    if os.name == "nt":
        rel = rel.lower()
        files = tuple(name.lower() for name in ADAPTER_FILES)
    else:
        files = ADAPTER_FILES
    return rel in files or rel.startswith(tuple(folder + "/" for folder in ADAPTER_DIRS))


def project_files() -> dict[str, str]:
    result = {}
    for name, source in template_files(TEMPLATES / "project"):
        path = f".{name}" if name.as_posix() == "gitignore" else name.as_posix()
        text = source.read_text(encoding="utf-8")
        if path == CONFIG:
            text = text.replace("{{engine}}", __version__).replace("format = 0", f"format = {config.FORMAT}")
        result[path] = text
    return result


def safe_destination(root: Path, name: str) -> Path:
    path = root / name
    if path.is_symlink() or path.resolve() != path or any(
        part.is_symlink() or (part.exists() and not part.is_dir()) for part in path.parents if part != root
        and part.is_relative_to(root)
    ):
        raise Problem(f"{name}：無法寫入連結或非目錄的上層路徑")
    if path.exists() and not path.is_file():
        raise Problem(f"{name}：已有同名的非普通檔案")
    return path


def adapter_updates(root: Path) -> dict[str, str]:
    result = {}
    for name, text in adapter_files(__version__).items():
        path = safe_destination(root, name)
        previous = path.read_text(encoding="utf-8") if path.is_file() else ""
        if path.is_file() and previous != text and GENERATED not in previous:
            raise Problem(f"{name}：已有自訂檔案，請先備份並把個人規則移到 profile/")
        result[name] = text
    return result


def obsolete_adapters(root: Path, updates: dict[str, str]) -> tuple[list[str], list[str]]:
    current = {root / name for name in updates}
    candidates = {root / name for name in ADAPTER_FILES}
    for name in ADAPTER_DIRS:
        folder = root / name
        if folder.is_dir() and folder.resolve() == folder and not folder.is_symlink() and not folder.is_junction():
            pending = [folder]
            while pending:
                for path in pending.pop().iterdir():
                    if not (path.is_symlink() or path.is_junction()) and path.is_dir():
                        pending.append(path)
                    else:
                        candidates.add(path)

    obsolete, custom = [], []
    marker = GENERATED.encode("utf-8")
    for path in sorted(candidates - current):
        name = path.relative_to(root).as_posix()
        if path.is_symlink() or path.is_junction() or path.resolve() != path:
            custom.append(name)
        elif path.is_file():
            (obsolete if marker in path.read_bytes() else custom).append(name)
    return obsolete, custom


def prune_adapter_dirs(root: Path, obsolete: list[str]) -> None:
    directories = set()
    for name in obsolete:
        for parent in Path(name).parents:
            rel = parent.as_posix()
            if rel in ADAPTER_DIRS or adapter_path(rel):
                directories.add(root / parent)
    for directory in sorted(directories, reverse=True):
        if directory.resolve() != directory or directory.is_symlink() or directory.is_junction():
            continue
        try:
            directory.rmdir()
        except OSError as error:
            if error.errno not in (errno.ENOTEMPTY, errno.EEXIST, errno.ENOENT):
                raise


def write_files(root: Path, updates: dict[str, str], *, remove: tuple[str, ...] = ()) -> None:
    with TemporaryDirectory(prefix=".seasoningway-adapters-", dir=root) as folder:
        stage = Path(folder)
        paths = {}
        for index, (name, text) in enumerate(updates.items()):
            target = safe_destination(root, name)
            source = stage / str(index)
            source.write_text(text, encoding="utf-8", newline="\n")
            if name.startswith(".githooks/"):
                source.chmod(0o755)
            paths[source] = target
        replace_paths(stage, paths, remove=[safe_destination(root, name) for name in remove])


def initialize_git(root: Path) -> None:
    existing = run_git(root, "rev-parse", "--show-toplevel")
    if existing.returncode:
        if (root / ".git").exists():
            raise Problem("讀不到既有 Git 工作區，請先檢查 Git 設定")
        checked_git(root, "init", "--initial-branch=main")
    if error := git_repo_problem(root):
        raise Problem(error)


def configure_git(root: Path) -> None:
    checked_git(root, "config", "--local", "core.hooksPath", ".githooks")


def cmd_init(args, root: Path, today) -> int:
    root.mkdir(parents=True, exist_ok=True)
    if (root / CONFIG).exists():
        cfg = config.load_config(root)
        if cfg.engine != __version__ or cfg.format != config.FORMAT:
            raise Problem(f"{CONFIG} 已存在，請執行 seasoningway upgrade")
    updates = adapter_updates(root)
    for name, text in project_files().items():
        path = safe_destination(root, name)
        if not path.is_file():
            updates[name] = text
        elif name == ".gitignore":
            previous = path.read_text(encoding="utf-8")
            if "/out/" not in previous.splitlines():
                updates[name] = previous.rstrip("\n") + "\n/out/\n"
    trips = root / TRIPS
    if trips.is_symlink() or (trips.exists() and not trips.is_dir()):
        raise Problem(f"{TRIPS}/：需要一般目錄")
    initialize_git(root)
    write_files(root, updates)
    trips.mkdir(exist_ok=True)
    configure_git(root)
    print(f"資料 repo 已備妥（引擎 {__version__}）。請用 seasoningway guide plan-trip 設定偏好。")
    return 0


def upgrade_config(text: str) -> str:
    original = tomllib.loads(text)
    expected = {**original, "engine": __version__}
    if original == expected:
        return text
    if "engine" not in original:
        return f'engine = "{__version__}"\n' + text
    for match in ENGINE_LINE.finditer(text):
        new = text[:match.start() + len(match[1])] + json.dumps(__version__) + text[match.end():]
        if tomllib.loads(new) == expected:
            return new
    raise Problem(f'{CONFIG}：請將 engine 設定整理為一行 engine = "X.Y.Z" 後再升級')


def cmd_upgrade(args, root: Path, today) -> int:
    path = safe_destination(root, CONFIG)
    if not path.is_file():
        raise Problem(f"找不到 {CONFIG}：請先執行 seasoningway init")
    cfg = config.load_config(root, validate=False)
    if not OLDEST <= cfg.format <= config.FORMAT:
        raise Problem(f"{CONFIG} 的格式為第 {cfg.format} 版；這版引擎可升級第 {OLDEST} 至 {config.FORMAT} 版")
    if error := git_repo_problem(root):
        raise Problem(error)
    updates = adapter_updates(root)
    obsolete, custom = obsolete_adapters(root, updates)
    updates[CONFIG] = upgrade_config(path.read_text(encoding="utf-8"))
    write_files(root, updates, remove=tuple(obsolete))
    for name in obsolete:
        print(f"已刪除舊轉接檔：{name}")
    prune_adapter_dirs(root, obsolete)
    for name in custom:
        print(f"提醒：保留自訂轉接檔 {name}")
    cmd_migrate(args, root, today)
    configure_git(root)
    print(f"已更新為引擎 {__version__}，請執行 seasoningway save 保存。")
    return 0
