"""安全更新產物與快取。"""
from pathlib import Path
from tempfile import NamedTemporaryFile


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False, mode="w", encoding="utf-8", newline="\n") as stream:
        temporary = Path(stream.name)
        try:
            stream.write(text)
        except BaseException:
            stream.close()
            temporary.unlink()
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def replace_paths(stage: Path, updates: dict[Path, Path]) -> None:
    """以已準備好的檔案或目錄更新目標，失敗時還原已更新的目標。"""
    moved = []
    try:
        for index, (source, target) in enumerate(updates.items()):
            backup = stage / f"previous-{index}"
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink():
                target.replace(backup)
            else:
                backup = None
            moved.append((source, target, backup))
            source.replace(target)
    except OSError:
        for source, target, backup in reversed(moved):
            if not source.exists():
                target.replace(source)
            if backup is not None:
                backup.replace(target)
        raise
