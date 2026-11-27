"""從套件範本建立指定版本的虛構測試 repo。"""
from pathlib import Path

from seasoningway.adapters import project_files
from seasoningway.files import write_text


def seed_project(root: Path) -> None:
    for name, text in project_files().items():
        write_text(root / name, text)
