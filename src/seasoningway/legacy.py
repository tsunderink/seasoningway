"""把第 3 版舊模板 repo 原地轉成資料 repo。"""
import argparse
import io
import posixpath
import re
import shutil
from contextlib import redirect_stdout
from itertools import chain
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import quote, unquote

from . import __version__
from .adapters import adapter_files, cmd_init, configure_git, project_files, safe_destination
from .checks import run_check
from .common import CONFIG, OUT, TRIPS, Problem
from .files import replace_paths, write_text
from .gitops import active_operation, checked_git, git_repo_problem, run_git
from .migration import generated_markdown
from .project import cmd_migrate

OLD_CONFIG = "data/trips.toml"
OLD_ENGINE = "core/trips.py"
OLD_DIRS = ("core", ".agents/skills", ".claude/agents", ".claude/skills", ".codex/agents")
OLD_FILES = ("AGENTS.md", "README.md", "LICENSE", ".claude/CLAUDE.md", ".claude/settings.json",
             ".codex/hooks.json", ".github/workflows/check.yml")
GIT_RULES = (".gitignore", ".gitattributes")
OLD_ATTRIBUTES = {
    "core/tripmap/leaflet-1.9.4.css", "/data/README.md", "data/trips/*/**/published.json",
    *(f"data/trips/*/{name}" for name in (
        "budget.md", "bookings.md", "itinerary.md", "map/index.html", "map/roads.txt",
        "map/base/places.json", "map/base/tiles.json",
    )),
}
DATA_LINK = re.compile(r"(?<!!)\]\(([^()\s#]+)(#[^()\s]*)?\)")


def find_template(start: Path) -> Path | None:
    return next((path for path in (start, *start.parents) if (path / OLD_CONFIG).is_file()), None)


def tree_files(root: Path, top: Path):
    for path in chain((top,), top.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink() or path.is_junction() or (not path.is_dir() and not path.is_file()):
            raise Problem(f"{rel}：遷移需要普通檔案或目錄，請先處理這個路徑")
        if path.is_file():
            yield rel


def old_engine_paths(root: Path) -> list[str]:
    paths = [name for name in (*OLD_DIRS, *OLD_FILES) if (root / name).exists() or (root / name).is_symlink()]
    changed = checked_git(root, "diff", "HEAD", "--name-only", "-z", "--no-renames", "--", *paths)
    changed += checked_git(root, "diff", "--cached", "--name-only", "-z", "--no-renames", "--", *paths)
    tracked = set(checked_git(root, "ls-files", "-z", "--", *paths).split("\0"))
    for name in paths:
        if (root / name).resolve() != root / name:
            raise Problem(f"{name}：舊引擎路徑含連結")
        for path in tree_files(root, root / name):
            if path not in tracked and not (path.endswith(".pyc") and "__pycache__" in Path(path).parts):
                changed += path + "\0"
    if changed:
        raise Problem("舊引擎有尚未保存的檔案，遷移會移除這些路徑，請先保存：\n" +
                      "\n".join(sorted(set(changed.split("\0")) - {""})))
    return paths


def clean_git_rules(root: Path, stage: Path) -> None:
    for name in GIT_RULES:
        path = safe_destination(root, name)
        if not path.is_file():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        if name == ".gitignore":
            lines = [line for line in lines if line.strip() != "!/core/"
                     and not line.startswith("# 使用者全域的 gitignore 有 core")]
        else:
            lines = [line for line in lines if (line.split() or [""])[0] not in OLD_ATTRIBUTES
                     and not line.startswith(("# 第三方原檔", "# 產生的檔案", "# 標成 linguist-generated",
                                              "# 這裡只放引擎產生的", "# 地圖頁（core/tripmap）"))]
            if "* text=auto eol=lf" not in lines:
                lines.insert(0, project_files()[name].strip())
        write_text(stage / name, "\n".join(lines).rstrip() + "\n")


def rebase_data_links(text: str, source: str, removed_index: bool) -> str:
    def replace(match: re.Match) -> str:
        target, fragment = match.groups()
        if re.match(r"^(?:[a-z][a-z0-9+.-]*:|/)", target, re.I):
            return match[0]
        original = posixpath.normpath(posixpath.join("data", source, unquote(target)))
        if original == OLD_CONFIG:
            new = CONFIG
        elif original == "data/README.md" and removed_index:
            new = TRIPS
        elif original == "data":
            new = "."
        else:
            new = original.removeprefix("data/")
        relative = posixpath.relpath(new, source)
        if relative == posixpath.normpath(unquote(target)):
            return match[0]
        return f"]({quote(relative, safe='/')}{'/' if target.endswith('/') else ''}{fragment or ''})"

    return DATA_LINK.sub(replace, text)


def prepare_data(root: Path, stage: Path) -> set[str]:
    data = root / "data"
    # 先檢查所有來源，再建立副本。避免讀到連結指向的其他資料。
    list(tree_files(root, data))
    extra = set()
    for source in sorted(data.iterdir()):
        name = source.name
        if name == "README.md" and source.is_file() and generated_markdown(source.read_text(encoding="utf-8")):
            continue
        target = CONFIG if name == "trips.toml" else name
        reserved = (".git", ".agents", ".claude", ".codex", ".github", ".githooks", OUT, "AGENTS.md", "CLAUDE.md", *GIT_RULES)
        if target.casefold() in {path.casefold() for path in reserved}:
            raise Problem(f"data/{name}：搬移後會占用資料 repo 保留的路徑")
        destination = root / target
        if target != "README.md" and (destination.exists() or destination.is_symlink()):
            raise Problem(f"{target} 已存在，請先處理搬移衝突")
        if target in (CONFIG, "README.md", "wishlist.md") and not source.is_file():
            raise Problem(f"data/{name} 必須是檔案")
        if source.is_dir():
            shutil.copytree(source, stage / target)
        else:
            shutil.copy2(source, stage / target)
        extra.add(target)
    config = stage / CONFIG
    text = config.read_text(encoding="utf-8")
    text = re.sub(r"\A# 這個 repo 的設定，core/trips\.py[^\n]*\n", "", text)
    text = text.replace("python3 core/trips.py migrate", "seasoningway migrate")
    write_text(config, f'engine = "{__version__}"\n' + text)
    for path in stage.rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        new = rebase_data_links(text, path.parent.relative_to(stage).as_posix(), "README.md" not in extra)
        if new != text:
            write_text(path, new)
    return extra


def replacement_paths(root: Path, stage: Path, data: set[str]) -> dict[Path, Path]:
    groups = (*OLD_DIRS[1:], ".githooks")
    adapters = adapter_files(__version__)
    names = set(groups) | {name for name in adapters if not name.startswith(tuple(group + "/" for group in groups))}
    names.update((*data, TRIPS, "profile", "wishlist.md", *GIT_RULES))
    for name in names:
        path = root / name
        if path.resolve() != path or path.is_junction() or path.is_symlink():
            raise Problem(f"{name}：遷移目標含連結")
        if name in (*groups, *adapters) and name not in (*OLD_DIRS, *OLD_FILES) and path.exists():
            raise Problem(f"{name} 已存在，請先處理轉接檔衝突")
    return {stage / name: root / name for name in sorted(names)}


def cmd_from_template(args, root: Path, today) -> int:
    if (root / CONFIG).is_file() and not (root / "data").exists() and not (root / OLD_ENGINE).exists():
        return cmd_migrate(args, root, today)
    if not (root / OLD_CONFIG).is_file() or not (root / OLD_ENGINE).is_file():
        raise Problem(f"找不到 {OLD_CONFIG} 或 {OLD_ENGINE}：需要舊模板 repo")
    if any(Path.cwd().is_relative_to(root / name) for name in ("data", *OLD_DIRS)):
        raise Problem("目前目錄在即將搬移的路徑內，請從 repo 根目錄執行遷移")
    if error := git_repo_problem(root):
        raise Problem(error)
    if operation := active_operation(root):
        raise Problem(f"Git 正在執行 {operation}，請先完成再遷移")
    if checked_git(root, "diff", "--cached", "--name-only", "-z", "--", "data", *GIT_RULES):
        raise Problem("舊資料或 Git 設定已有暫存改動，請先保存再遷移")

    legacy = old_engine_paths(root)
    with TemporaryDirectory(prefix=".seasoningway-migrate-", dir=root) as directory:
        temp = Path(directory)
        stage = temp / "data-repo"
        stage.mkdir()
        moved = prepare_data(root, stage)
        clean_git_rules(root, stage)
        # 在同一磁碟準備獨立的 Git 工作區，以便重用 init 和 check。
        checked_git(stage, "init", "--initial-branch=main")
        output = io.StringIO()
        with redirect_stdout(output):
            cmd_migrate(argparse.Namespace(), stage, today)
            cmd_init(argparse.Namespace(), stage, today)
        problems, _ = run_check(stage, today)
        if problems:
            raise Problem("遷移副本未通過 check：\n" + "\n".join(problems))

        updates = replacement_paths(root, stage, moved)
        obsolete = [root / name for name in ("data", *legacy) if root / name not in updates.values()]
        previous = run_git(root, "config", "--local", "--get", "core.hooksPath")
        configure_git(root)
        try:
            replace_paths(temp, updates, remove=obsolete)
        except OSError:
            if previous.returncode == 0:
                checked_git(root, "config", "--local", "core.hooksPath", previous.stdout.rstrip("\n"))
            else:
                checked_git(root, "config", "--local", "--unset", "core.hooksPath")
            raise
    print(output.getvalue().strip())
    print("舊模板資料已搬移。請執行 seasoningway render 檢查頁面，再用 seasoningway save 保存。")
    return 0
