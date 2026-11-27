"""安全更新產物與快取。"""
from collections.abc import Iterable
from itertools import chain
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


def replace_paths(stage: Path, updates: dict[Path, Path], *, remove: Iterable[Path] = ()) -> None:
    """更新已準備好的路徑並移除舊路徑，失敗時還原。"""
    moved = []
    try:
        for index, (source, target) in enumerate(chain(updates.items(), ((None, item) for item in remove))):
            backup = stage / f"previous-{index}"
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink():
                target.replace(backup)
            else:
                backup = None
            moved.append((source, target, backup))
            if source is not None:
                source.replace(target)
    except OSError:
        for source, target, backup in reversed(moved):
            if source is not None and not source.exists():
                target.replace(source)
            if backup is not None:
                backup.replace(target)
        raise
