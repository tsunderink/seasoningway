"""發布紀錄及檔案雜湊。"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path, PurePosixPath, PureWindowsPath

from .common import TRIPS, Problem


PUBLISHED = "published.json"  # artifact 上次發布的狀態：{"slug", "version", "include": [glob…], "files": {路徑: sha256}}


def publish_hashes(folder: Path, include: list[str]) -> dict[str, str]:
    files = sorted({f for pattern in include for f in folder.glob(pattern) if f.is_file()})
    return {f.relative_to(folder).as_posix(): hashlib.sha256(f.read_bytes()).hexdigest() for f in files}


def bad_include(include) -> bool:
    """發布的檔案樣式要是非空的 list，每項是資料夾裡的相對路徑樣式（不是絕對路徑、不含 ..）。"""
    return not isinstance(include, list) or not include or not all(
        isinstance(p, str) and "\\" not in p and not PureWindowsPath(p).drive
        and PurePosixPath(p).parts and not PurePosixPath(p).is_absolute() and ".." not in PurePosixPath(p).parts
        for p in include)


def published_states(root: Path) -> tuple[dict[Path, dict], list[str]]:
    """各 artifact 資料夾的 published.json：({資料夾: 內容}, 讀不了的檔的 ✗)。"""
    states, problems = {}, []
    for state_path in sorted((root / TRIPS).glob(f"*/**/{PUBLISHED}")):
        rel = state_path.parent.relative_to(root).as_posix()
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if not isinstance(state["slug"], str) or bad_include(state["include"]) or not isinstance(state["files"], dict):
                raise TypeError
            states[state_path.parent] = {"slug": state["slug"], "include": state["include"], "files": state["files"]}
        except (ValueError, KeyError, TypeError):
            problems.append(f"{rel}/{PUBLISHED} 讀不了（例如合併衝突、格式不對）：用 seasoningway published {rel} <版本號> "
                            "--slug <slug> --include <樣式…> 重建")
    return states, problems


def unpublished(root: Path) -> list[str]:
    """發布過的 artifact（資料夾裡有 published.json）：本地檔案和上次發布的不一樣就提醒重新發布。"""
    warnings = []
    for folder, state in published_states(root)[0].items():
        now, then = publish_hashes(folder, state["include"]), state["files"]
        changed = sorted(k for k in now.keys() | then.keys() if now.get(k) != then.get(k))
        if changed:
            rel = folder.relative_to(root).as_posix()
            more = f" 等 {len(changed)} 個" if len(changed) > 3 else ""
            warnings.append(f"{rel}：{'、'.join(changed[:3])}{more}和上次發布的不一樣，重新發布 artifact {state['slug']}"
                            f"（新增、改了的放 files，刪掉的放 remove），再 seasoningway published {rel} 版本號")
    return warnings


def cmd_published(args: argparse.Namespace, root: Path, today: date) -> int:
    root = root.resolve()
    folder = (root / args.folder).resolve()
    if not folder.is_dir() or not folder.is_relative_to(root / TRIPS):
        raise Problem(f"{args.folder}：要是 {TRIPS}/ 裡的資料夾（check 只比對那裡的 {PUBLISHED}）")
    state = published_states(root)[0].get(folder, {})  # 沒有或讀不了（例如合併衝突）就重建
    slug, include = args.slug or state.get("slug"), args.include or state.get("include")
    if not slug or not include:
        raise Problem(f"{args.folder} 沒有可用的 {PUBLISHED}：加 --slug <artifact 的 slug> --include <要發布的檔案樣式…> 建立")
    if bad_include(include):
        raise Problem("--include 的樣式要是資料夾裡的相對路徑（例如 index.html、'photos/*.jpg'），不能是空的、絕對路徑或含 ..")
    files = publish_hashes(folder, include)
    state = {"slug": slug, "version": args.version, "include": include, "files": files}
    (folder / PUBLISHED).write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"記下 {len(files)} 個檔案的發布狀態（版本 {args.version}）")
    return 0
