"""從套件範本建立指定版本的虛構測試 repo。"""
import shutil
from pathlib import Path
from importlib.resources import as_file

from seasoningway import __version__, config, project
from seasoningway.common import CONFIG


def seed_project(root: Path) -> None:
    with as_file(project.TEMPLATES / "project") as source:
        shutil.copytree(source, root, dirs_exist_ok=True)
    file = root / CONFIG
    text = file.read_text(encoding="utf-8").replace("{{engine}}", __version__)
    file.write_text(text.replace("format = 0", f"format = {config.FORMAT}"), encoding="utf-8", newline="\n")
    (root / ".gitignore").write_text("/out/\n", encoding="utf-8", newline="\n")
