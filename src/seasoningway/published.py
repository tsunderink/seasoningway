"""發布紀錄與來源雜湊。"""
import argparse
import hashlib
from datetime import date
from pathlib import Path

from .common import TRIPS, Problem
from .files import write_text
from .records import dump_json, parse_json, schema_errors, schema_url
from .trips import load_trip

PUBLISHED = "published.json"


def publication_paths(trip: Path) -> list[Path]:
    return sorted(path for path in trip.rglob(PUBLISHED) if path.is_file())


def publish_hashes(trip: Path) -> dict[str, str]:
    hashes = {}
    for path in sorted(trip.rglob("*")):
        relative = path.relative_to(trip)
        if (path.is_file() and not path.is_symlink() and path.name != PUBLISHED
                and not any(part in (".git", "__pycache__") or part.startswith(".fetch-") for part in relative.parts)):
            with path.open("rb") as stream:
                hashes[relative.as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
    return hashes


def published_states(root: Path) -> tuple[dict[Path, dict], list[str]]:
    states, problems = {}, []
    for path in publication_paths(root / TRIPS):
        rel = path.relative_to(root).as_posix()
        try:
            state = parse_json(path.read_text(encoding="utf-8"), rel)
            errors = schema_errors("published", state, rel)
        except (Problem, OSError, UnicodeError) as error:
            errors = [str(error)]
        if errors:
            problems.extend(errors)
        else:
            states[path] = state
    return states, problems


def unpublished(root: Path, states: dict[Path, dict]) -> list[str]:
    warnings = []
    hashes = {}
    for path, state in states.items():
        rel = path.relative_to(root)
        trip = root / TRIPS / rel.parts[1]
        if trip not in hashes:
            hashes[trip] = publish_hashes(trip)
        now, then = hashes[trip], state["inputs"]
        changed = sorted(k for k in now.keys() | then.keys() if now.get(k) != then.get(k))
        if changed:
            more = f" 等 {len(changed)} 個" if len(changed) > 3 else ""
            warnings.append(f"{rel.as_posix()}：{'、'.join(changed[:3])}{more}和上次發布的不一樣，"
                            f"重新產生並發布 artifact {state['slug']} 後再執行 seasoningway published")
    return warnings


def cmd_published(args: argparse.Namespace, root: Path, today: date) -> int:
    raw = Path(args.folder)
    if len(raw.parts) == 1:
        trip = load_trip(root, args.folder)
        folder = trip.path.resolve()
        legacy = folder / "map" / PUBLISHED
        if not (folder / PUBLISHED).exists() and legacy.is_file():
            folder = legacy.parent
    else:
        folder = (root / raw).resolve()
        trip = None
    base = root.resolve() / TRIPS
    if not folder.is_dir() or not folder.is_relative_to(base) or folder == base:
        raise Problem(f"{args.folder}：要是旅程代號，或 {TRIPS}/ 裡的資料夾")
    if trip is None:
        trip = load_trip(root, folder.relative_to(base).parts[0])
    path = folder / PUBLISHED
    states, _ = published_states(root)
    slug = args.slug or states.get(path, {}).get("slug")
    if not slug:
        raise Problem(f"{args.folder} 沒有可用的 {PUBLISHED}：第一次記錄時加 --slug <artifact 的 slug>")
    inputs = publish_hashes(trip.path)
    state = {"$schema": schema_url("published"), "slug": slug, "version": args.version, "inputs": inputs}
    errors = schema_errors("published", state, path.relative_to(root.resolve()).as_posix())
    if errors:
        raise Problem("\n".join(errors))
    write_text(path, dump_json(state))
    print(f"記下 {len(inputs)} 個來源與快取檔案的雜湊（版本 {args.version}）")
    return 0
