"""驗證資料並輸出頁面。"""
import argparse
import re
import shutil
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from .checks import LINK_RE, inspect_data, print_issues
from .common import OUT, Problem
from .tripmap import render as map_render
from .files import replace_paths
from .trips import load_trip, load_trips
from .views import render_index


def view_links(text: str, slug: str) -> str:
    def rebase(match: re.Match) -> str:
        target = match[1]
        if re.match(r"[a-z][a-z0-9+.-]*:|[#/]", target, re.I):
            return match[0]
        return f"](../../trips/{slug}/{target})"

    return LINK_RE.sub(rebase, text)


def copy_map_assets(trip: Path, out: Path) -> None:
    for source, relative in (
        (trip / "cache/roads.txt", "roads.txt"),
        (trip / "cache/base/tiles", "base/tiles"),
        (trip / "map/photos", "photos"),
    ):
        if not source.resolve().is_relative_to(trip):
            raise Problem(f"{source}：地圖資源必須在旅程目錄內")
        target = out / relative
        if source.is_dir():
            if source.is_symlink() or source.is_junction() or any(path.is_symlink() or path.is_junction() for path in source.rglob("*")):
                raise Problem(f"{source}：地圖資源目錄不能含檔案或目錄連結")
            shutil.copytree(source, target)
        elif source.is_file():
            if source.is_symlink():
                raise Problem(f"{source}：地圖資源不能是檔案連結")
            shutil.copyfile(source, target)


def cmd_render(args: argparse.Namespace, root: Path, today: date) -> int:
    if args.slug:
        load_trip(root, args.slug)
    report = inspect_data(root, today)
    print_issues(report.problems, report.warnings)
    if report.problems:
        return 1

    out = root / OUT
    if out.is_symlink() or out.is_junction():
        raise Problem(f"{OUT}/ 必須是資料 repo 裡的實際目錄")
    out.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix=".render-", dir=out) as directory:
        stage = Path(directory)
        updates = {}
        for slug, pages in report.pages.items():
            if args.slug and slug != args.slug:
                continue
            folder = stage / slug
            for name, text in pages.items():
                dest = folder / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.suffix == ".md":
                    text = view_links(text, slug)
                dest.write_text(text, encoding="utf-8", newline="\n")
            if slug in report.maps:
                dest = folder / "map/index.html"
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(map_render.render(report.maps[slug]), encoding="utf-8", newline="\n")
                copy_map_assets(report.maps[slug].trip.dir, dest.parent)
            updates[folder] = out / slug
        if not args.slug:
            index = stage / "index.md"
            index.write_text(render_index(load_trips(root)), encoding="utf-8", newline="\n")
            updates[index] = out / index.name
        replace_paths(stage, updates)
    print(f"已產生 {OUT}/{args.slug or ''}")
    return 0
